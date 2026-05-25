from __future__ import annotations

import asyncio
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


# ---- AgentState ----

@dataclass
class AgentState:
    agent_id: str = field(default_factory=lambda: f"agent_{uuid.uuid4().hex[:8]}")
    agent_name: str = "DeepAnalyseAgent"
    parent_id: str | None = None
    task: str = ""
    task_id: str = ""
    role: str = ""  # classify / scan / analyze / cross-link / review / root
    messages: list[dict[str, Any]] = field(default_factory=list)
    iteration: int = 0
    max_iterations: int = 300
    completed: bool = False
    stop_requested: bool = False
    waiting_for_input: bool = False
    waiting_start_time: str | None = None
    waiting_timeout: int = 600
    final_result: dict[str, Any] | None = None
    _wake_event: asyncio.Event = field(default_factory=asyncio.Event)
    _event_loop: asyncio.AbstractEventLoop | None = field(default=None)

    def __post_init__(self) -> None:
        try:
            self._event_loop = asyncio.get_running_loop()
        except RuntimeError:
            self._event_loop = None

    def signal_wake(self) -> None:
        """Thread-safe wake signal. Can be called from any thread.

        When the event loop is running (production), schedules the wake via
        *call_soon_threadsafe* so that cross-thread callers are handled
        correctly.  When the loop is not running (tests, or during
        initialisation), falls back to a direct ``.set()`` which is safe
        because we are in the same thread.
        """
        if self._event_loop is not None and self._event_loop.is_running():
            self._event_loop.call_soon_threadsafe(self._wake_event.set)
        else:
            self._wake_event.set()

    def add_message(self, role: str, content: str) -> None:
        self.messages.append({"role": role, "content": content})
        if self.waiting_for_input:
            self.signal_wake()

    def enter_waiting_state(self) -> None:
        self.waiting_for_input = True
        self.waiting_start_time = datetime.now(timezone.utc).isoformat()

    def resume_from_waiting(self) -> None:
        self.waiting_for_input = False
        self.waiting_start_time = None
        self.signal_wake()

    async def wait_for_wake(self, timeout: float = 0.5) -> None:
        try:
            await asyncio.wait_for(self._wake_event.wait(), timeout=timeout)
            self._wake_event.clear()
        except asyncio.TimeoutError:
            pass

    def should_stop(self) -> bool:
        return self.stop_requested or self.completed or self.iteration >= self.max_iterations

    def increment_iteration(self) -> None:
        self.iteration += 1

    def get_conversation_history(self) -> list[dict[str, Any]]:
        return list(self.messages)

    def has_waiting_timeout(self) -> bool:
        if self.waiting_timeout == 0:
            return False
        if not self.waiting_for_input or not self.waiting_start_time:
            return False
        if self.stop_requested or self.completed or self.iteration >= self.max_iterations:
            return False
        try:
            started = datetime.fromisoformat(self.waiting_start_time)
            elapsed = (datetime.now(timezone.utc) - started).total_seconds()
            return elapsed > self.waiting_timeout
        except ValueError:
            return False


# ---- Global State ----
#
# Concurrency notes:
#   All writes to `_agent_graph["nodes"]` and `_agent_graph["edges"]` MUST
#   hold `_agent_graph_lock`. Readers (including iteration) SHOULD also
#   acquire the lock to avoid TOCTOU races, unless the caller can prove
#   single-threaded access.

_agent_graph: dict[str, Any] = {"nodes": {}, "edges": []}
_root_agent_id: str | None = None
_agent_messages: dict[str, list[dict[str, Any]]] = {}
_running_agents: dict[str, threading.Thread] = {}
_agent_instances: dict[str, Any] = {}
_agent_states: dict[str, Any] = {}
_agent_graph_lock = threading.Lock()


# ---- Helpers ----

def _generate_message_id() -> str:
    return f"msg_{uuid.uuid4().hex[:8]}"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---- Five Core Graph Primitives ----


def create_agent(
    agent_state: AgentState,
    task: str,
    name: str,
    inherit_context: bool = True,
) -> dict[str, Any]:
    """Create and spawn a new agent as a daemon thread."""
    try:
        parent_id = agent_state.agent_id

        child_state = AgentState(
            agent_name=name,
            parent_id=parent_id,
            task=task,
            max_iterations=agent_state.max_iterations,
            waiting_timeout=agent_state.waiting_timeout,
        )

        parent_name = _agent_graph["nodes"].get(parent_id, {}).get("name", "Unknown Parent")
        context_status = (
            "inherited conversation context from your parent"
            if inherit_context else "started with a fresh context"
        )
        delegation_xml = f"""<agent_delegation>
    <identity>
        You are NOT your parent agent. You are a NEW, SEPARATE sub-agent.
        Your Info: {name} ({child_state.agent_id})
        Parent Info: {parent_name} ({parent_id})
    </identity>
    <your_task>{task}</your_task>
    <instructions>
        - You have {context_status}
        - Inherited context is for BACKGROUND ONLY - don't continue parent's work
        - Focus EXCLUSIVELY on your delegated task above
        - Work independently with your own approach
        - Use agent_finish when complete to report back to parent
        - You are a SPECIALIST for this specific task
    </instructions>
</agent_delegation>"""

        inherited_messages = []
        if inherit_context:
            inherited_messages = agent_state.get_conversation_history()

        node = {
            "id": child_state.agent_id,
            "name": name,
            "task": task,
            "status": "running",
            "parent_id": parent_id,
            "role": "",
            "created_at": _now_iso(),
            "finished_at": None,
            "result": None,
        }

        with _agent_graph_lock:
            _agent_graph["nodes"][child_state.agent_id] = node
            _agent_states[child_state.agent_id] = child_state

            delegation_edge = {
                "from": parent_id,
                "to": child_state.agent_id,
                "type": "delegation",
                "created_at": _now_iso(),
            }
            _agent_graph["edges"].append(delegation_edge)

            _agent_messages[child_state.agent_id] = []

        child_state.add_message("user", delegation_xml)

        if inherit_context and inherited_messages:
            child_state.add_message("user", "<inherited_context_from_parent>")
            for msg in inherited_messages:
                child_state.add_message(msg["role"], msg["content"])
            child_state.add_message("user", "</inherited_context_from_parent>")

        # Resolve LLM provider from parent agent instance
        parent_agent = _agent_instances.get(parent_id)
        llm_provider = getattr(parent_agent, "_llm", None) if parent_agent is not None else None

        agent = None
        try:
            from nano_strix.agents.deep_analysis_lib.deep_agent import DeepAnalyseAgent  # type: ignore[import-untyped]  # noqa: F811
        except ImportError:
            with _agent_graph_lock:
                _agent_instances[child_state.agent_id] = None
        else:
            agent = DeepAnalyseAgent(state=child_state, llm_provider=llm_provider)

            with _agent_graph_lock:
                _agent_instances[child_state.agent_id] = agent

            if llm_provider is not None:
                thread = threading.Thread(
                    target=_run_agent_in_thread,
                    args=(agent, child_state),
                    daemon=True,
                    name=f"DeepAnalyse-{name}-{child_state.agent_id}",
                )
                thread.start()
                _running_agents[child_state.agent_id] = thread

    except Exception as e:
        return {"success": False, "error": f"Failed to create agent: {e}", "agent_id": None}
    else:
        started = llm_provider is not None
        return {
            "success": True,
            "agent_id": child_state.agent_id,
            "message": (
                f"Agent '{name}' created and started asynchronously"
                if started
                else f"Agent '{name}' registered (no LLM provider; thread not started)"
            ),
            "agent_info": {
                "id": child_state.agent_id,
                "name": name,
                "status": "running",
                "parent_id": parent_id,
            },
        }


def send_message_to_agent(
    agent_state: AgentState,
    target_agent_id: str,
    message: str,
    message_type: str = "information",
    priority: str = "normal",
) -> dict[str, Any]:
    """Send a message to another agent's inbox."""
    try:
        sender_id = agent_state.agent_id

        if target_agent_id not in _agent_graph["nodes"]:
            return {
                "success": False,
                "error": f"Target agent '{target_agent_id}' not found in graph",
                "message_id": None,
            }

        msg_data = {
            "id": _generate_message_id(),
            "from": sender_id,
            "to": target_agent_id,
            "content": message,
            "message_type": message_type,
            "priority": priority,
            "timestamp": _now_iso(),
            "delivered": True,
            "read": False,
        }

        if target_agent_id not in _agent_messages:
            _agent_messages[target_agent_id] = []

        _agent_messages[target_agent_id].append(msg_data)

        with _agent_graph_lock:
            _agent_graph["edges"].append({
                "from": sender_id,
                "to": target_agent_id,
                "type": "message",
                "message_id": msg_data["id"],
                "message_type": message_type,
                "priority": priority,
                "created_at": _now_iso(),
            })

        target_state = _agent_states.get(target_agent_id)
        if target_state is not None and target_state.waiting_for_input:
            target_state.signal_wake()

        sender_name = _agent_graph["nodes"].get(sender_id, {}).get("name", agent_state.agent_name)
        target_name = _agent_graph["nodes"][target_agent_id]["name"]

    except Exception as e:
        return {"success": False, "error": f"Failed to send message: {e}", "message_id": None}
    else:
        return {
            "success": True,
            "message_id": msg_data["id"],
            "message": f"Message sent from '{sender_name}' to '{target_name}'",
            "delivery_status": "delivered",
            "target_agent": {
                "id": target_agent_id,
                "name": target_name,
                "status": _agent_graph["nodes"][target_agent_id]["status"],
            },
        }


def wait_for_message(
    agent_state: AgentState,
    reason: str = "Waiting for messages from other agents",
) -> dict[str, Any]:
    """Put the agent into waiting state until messages arrive."""
    try:
        agent_id = agent_state.agent_id
        agent_name = agent_state.agent_name

        agent_state.enter_waiting_state()

        if agent_id in _agent_graph["nodes"]:
            _agent_graph["nodes"][agent_id]["status"] = "waiting"
            _agent_graph["nodes"][agent_id]["waiting_reason"] = reason

    except Exception as e:
        return {"success": False, "error": f"Failed to enter waiting state: {e}", "status": "error"}
    else:
        return {
            "success": True,
            "status": "waiting",
            "message": f"Agent '{agent_name}' is now waiting for messages",
            "reason": reason,
            "agent_info": {
                "id": agent_id,
                "name": agent_name,
                "status": "waiting",
            },
            "resume_conditions": [
                "Message from another agent",
                "Message from user",
                "Waiting timeout reached",
            ],
        }


def agent_finish(
    agent_state: AgentState,
    result_summary: str,
    findings: list[str] | None = None,
    success: bool = True,
    report_to_parent: bool = True,
    final_recommendations: list[str] | None = None,
) -> dict[str, Any]:
    """Complete the agent and optionally report to parent."""
    try:
        if agent_state.parent_id is None:
            return {
                "agent_completed": False,
                "error": (
                    "This tool can only be used by subagents. "
                    "Root agents must use root_finish instead."
                ),
                "parent_notified": False,
            }

        agent_id = agent_state.agent_id

        if agent_id not in _agent_graph["nodes"]:
            return {"agent_completed": False, "error": "Current agent not found in graph"}

        agent_node = _agent_graph["nodes"][agent_id]
        findings = findings or []
        final_recommendations = final_recommendations or []

        agent_node["status"] = "finished" if success else "failed"
        agent_node["finished_at"] = _now_iso()
        agent_node["result"] = {
            "summary": result_summary,
            "findings": findings,
            "success": success,
            "recommendations": final_recommendations,
        }

        parent_notified = False
        if report_to_parent and agent_node["parent_id"]:
            parent_id = agent_node["parent_id"]

            if parent_id in _agent_graph["nodes"]:
                findings_xml = "\n".join(
                    f"        <finding>{f}</finding>" for f in findings
                )
                recs_xml = "\n".join(
                    f"        <recommendation>{r}</recommendation>" for r in final_recommendations
                )

                report_message = f"""<agent_completion_report>
    <agent_info>
        <agent_name>{agent_node["name"]}</agent_name>
        <agent_id>{agent_id}</agent_id>
        <task>{agent_node["task"]}</task>
        <status>{"SUCCESS" if success else "FAILED"}</status>
        <completion_time>{agent_node["finished_at"]}</completion_time>
    </agent_info>
    <results>
        <summary>{result_summary}</summary>
        <findings>
{findings_xml}
        </findings>
        <recommendations>
{recs_xml}
        </recommendations>
    </results>
</agent_completion_report>"""

                if parent_id not in _agent_messages:
                    _agent_messages[parent_id] = []

                _agent_messages[parent_id].append({
                    "id": f"report_{uuid.uuid4().hex[:8]}",
                    "from": agent_id,
                    "to": parent_id,
                    "content": report_message,
                    "message_type": "information",
                    "priority": "high",
                    "timestamp": _now_iso(),
                    "delivered": True,
                    "read": False,
                })

                parent_state = _agent_states.get(parent_id)
                if parent_state is not None and parent_state.waiting_for_input:
                    parent_state.signal_wake()

                parent_notified = True

        _running_agents.pop(agent_id, None)

    except Exception as e:
        return {
            "agent_completed": False,
            "error": f"Failed to complete agent: {e}",
            "parent_notified": False,
        }
    else:
        return {
            "agent_completed": True,
            "parent_notified": parent_notified,
            "completion_summary": {
                "agent_id": agent_id,
                "agent_name": agent_node["name"],
                "task": agent_node["task"],
                "success": success,
                "findings_count": len(findings),
                "has_recommendations": bool(final_recommendations),
                "finished_at": agent_node["finished_at"],
            },
        }


def view_agent_graph(agent_state: AgentState) -> dict[str, Any]:
    """Return a text view of the current agent tree."""
    try:
        lines = ["=== AGENT GRAPH ==="]

        def _build_tree(agent_id: str, depth: int = 0) -> None:
            node = _agent_graph["nodes"].get(agent_id)
            if node is None:
                return
            indent = "  " * depth
            you = " <- This is you" if agent_id == agent_state.agent_id else ""
            lines.append(f"{indent}* {node['name']} ({agent_id}){you}")
            lines.append(f"{indent}  Task: {node['task'][:80]}")
            lines.append(f"{indent}  Status: {node['status']}")

            children = [
                e["to"] for e in _agent_graph["edges"]
                if e["type"] == "delegation" and e.get("from") == agent_id
            ]
            if children:
                lines.append(f"{indent}  Children:")
                for cid in children:
                    _build_tree(cid, depth + 2)

        root_id = _root_agent_id
        if not root_id and _agent_graph["nodes"]:
            for aid, n in _agent_graph["nodes"].items():
                if n.get("parent_id") is None:
                    root_id = aid
                    break
            if not root_id:
                root_id = next(iter(_agent_graph["nodes"].keys()))

        if root_id and root_id in _agent_graph["nodes"]:
            _build_tree(root_id)
        else:
            lines.append("No agents in graph yet")

        statuses = [n["status"] for n in _agent_graph["nodes"].values()]
        summary = {
            "total": len(_agent_graph["nodes"]),
            "running": statuses.count("running"),
            "waiting": statuses.count("waiting"),
            "finished": statuses.count("finished"),
            "failed": statuses.count("failed"),
        }

    except Exception as e:
        return {"error": f"Failed to view agent graph: {e}", "graph_structure": "Error"}
    else:
        return {"graph_structure": "\n".join(lines), "summary": summary}


# ---- Agent Thread Runner ----


def _run_agent_in_thread(
    agent: Any,
    state: AgentState,
) -> dict[str, Any]:
    """Entry point for each agent daemon thread."""
    import asyncio as aio
    loop = aio.new_event_loop()
    aio.set_event_loop(loop)
    try:
        result = loop.run_until_complete(agent.agent_loop())
    except Exception:
        agent_id = state.agent_id
        if agent_id in _agent_graph["nodes"]:
            _agent_graph["nodes"][agent_id]["status"] = "error"
            _agent_graph["nodes"][agent_id]["finished_at"] = _now_iso()
        _running_agents.pop(agent_id, None)
        raise
    else:
        agent_id = state.agent_id
        if agent_id in _agent_graph["nodes"]:
            node = _agent_graph["nodes"][agent_id]
            if node["status"] not in ("finished", "failed", "error"):
                node["status"] = "completed" if not state.stop_requested else "stopped"
            node["finished_at"] = _now_iso()
            node["result"] = result
        _running_agents.pop(agent_id, None)
        return result or {}
    finally:
        loop.close()
