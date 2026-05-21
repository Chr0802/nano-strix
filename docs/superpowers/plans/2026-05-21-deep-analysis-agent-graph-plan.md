# Deep Analysis Agent Graph 重构实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 per_file stage 从固定三阶段流水线重构为基于 strix agents_graph 模式的递归式 root-agent/sub-agent 架构（deep-analysis stage）。

**Architecture:** 新增 `graph.py` 提供与 strix 对齐的进程内 agent graph 基础设施（全局状态 + 5 个核心原语），新增 `DeepAnalyseAgent` 基类 + 6 种子类 agent（Root/Classify/Scan/Analyze/CrossLink/Review），统一 prompt 模板 + 参数化角色。LLM 层新增 `OpenAICompatibleProvider`，沙箱层实现 DockerSandbox + Tool Server，新增 skills 技能加载系统。外部 IPC 接口不变。

**Tech Stack:** Python 3.10+, dataclasses, asyncio, threading, Anthropic SDK, openai SDK, Docker SDK for Python, Flask (容器内 tool server), pytest

---

### Task 1: AgentState + Graph 全局状态

**Files:**
- Create: `src/nano_strix/agents/per_file_lib/graph.py`
- Test: `tests/test_graph_state.py`

- [ ] **Step 1: 编写 AgentState 和全局状态的失败测试**

```python
# tests/test_graph_state.py
import asyncio
import pytest
from nano_strix.agents.per_file_lib.graph import AgentState


def test_agent_state_defaults():
    state = AgentState()
    assert state.agent_id.startswith("agent_")
    assert len(state.agent_id) == 14  # "agent_" + 8 hex
    assert state.agent_name == "DeepAnalyseAgent"
    assert state.parent_id is None
    assert state.iteration == 0
    assert state.max_iterations == 300
    assert state.completed is False
    assert state.waiting_for_input is False


def test_agent_state_add_message():
    state = AgentState()
    state.add_message("user", "hello")
    assert len(state.messages) == 1
    assert state.messages[0] == {"role": "user", "content": "hello"}


def test_agent_state_wake_on_message():
    state = AgentState()
    state.enter_waiting_state()
    assert state.waiting_for_input is True
    # adding a message while waiting should set the wake event
    state.add_message("user", "wake up")
    # wake_event should now be set
    assert state._wake_event.is_set()


@pytest.mark.asyncio
async def test_agent_state_wait_for_wake_timeout():
    state = AgentState()
    # should return after timeout since no one sets the event
    await state.wait_for_wake(timeout=0.1)


@pytest.mark.asyncio
async def test_agent_state_wait_for_wake_signalled():
    state = AgentState()
    state.enter_waiting_state()

    async def signal_later():
        await asyncio.sleep(0.05)
        state.resume_from_waiting()

    import asyncio as aio
    done, pending = await aio.wait(
        [aio.create_task(state.wait_for_wake(timeout=1.0)),
         aio.create_task(signal_later())],
        return_when=aio.FIRST_COMPLETED
    )
    # If wait_for_wake returned first, it didn't block forever
    assert any(not t.cancelled() for t in done)


def test_agent_state_should_stop_max_iterations():
    state = AgentState()
    state.iteration = 300
    assert state.should_stop() is True


def test_agent_state_increment_iteration():
    state = AgentState()
    state.increment_iteration()
    assert state.iteration == 1


def test_graph_globals_exist():
    from nano_strix.agents.per_file_lib import graph
    assert hasattr(graph, '_agent_graph')
    assert graph._agent_graph == {"nodes": {}, "edges": []}
    assert graph._root_agent_id is None
    assert isinstance(graph._agent_messages, dict)
    assert isinstance(graph._running_agents, dict)
    assert isinstance(graph._agent_instances, dict)
    assert isinstance(graph._agent_states, dict)
```

- [ ] **Step 2: 运行测试验证失败**

```bash
.venv/bin/pytest tests/test_graph_state.py -v
# Expected: 全部 FAIL (ModuleNotFoundError)
```

- [ ] **Step 3: 实现 AgentState + 全局状态**

```python
# src/nano_strix/agents/per_file_lib/graph.py
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

    def add_message(self, role: str, content: str) -> None:
        self.messages.append({"role": role, "content": content})
        if self.waiting_for_input:
            self._wake_event.set()

    def enter_waiting_state(self) -> None:
        self.waiting_for_input = True
        self.waiting_start_time = datetime.now(timezone.utc).isoformat()

    def resume_from_waiting(self) -> None:
        self.waiting_for_input = False
        self.waiting_start_time = None
        self._wake_event.set()

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
```

- [ ] **Step 4: 运行测试验证通过**

```bash
.venv/bin/pytest tests/test_graph_state.py -v
# Expected: 全部 PASS
```

- [ ] **Step 5: Commit**

```bash
git add src/nano_strix/agents/per_file_lib/graph.py tests/test_graph_state.py
git commit -m "feat: add AgentState dataclass and graph global state"
```

---

### Task 2: 五个核心 Graph 原语 (create_agent, wait_for_message, agent_finish, send_message, view_graph)

**Files:**
- Modify: `src/nano_strix/agents/per_file_lib/graph.py`
- Test: `tests/test_graph_primitives.py`

- [ ] **Step 1: 编写五个原语的测试**

```python
# tests/test_graph_primitives.py
import pytest
from nano_strix.agents.per_file_lib.graph import (
    AgentState,
    create_agent,
    wait_for_message,
    send_message_to_agent,
    agent_finish,
    view_agent_graph,
    _agent_graph,
    _agent_messages,
    _running_agents,
    _root_agent_id,
)


def _cleanup_graph():
    """Reset global state between tests."""
    _agent_graph["nodes"].clear()
    _agent_graph["edges"].clear()
    _agent_messages.clear()
    _running_agents.clear()
    _agent_graph["nodes"] = {}
    _agent_graph["edges"] = []
    from nano_strix.agents.per_file_lib.graph import _agent_instances, _agent_states, _root_agent_id as root_id_ref
    _agent_instances.clear()
    _agent_states.clear()
    import nano_strix.agents.per_file_lib.graph as g
    g._root_agent_id = None


class TestCreateAgent:
    def test_create_agent_returns_agent_id(self):
        _cleanup_graph()
        state = AgentState(agent_name="TestRoot")
        result = create_agent(state, task="analyze login.py", name="LoginAnalyzer")
        assert result["success"] is True
        assert result["agent_id"].startswith("agent_")
        assert result["agent_info"]["name"] == "LoginAnalyzer"

    def test_create_agent_registers_node(self):
        _cleanup_graph()
        state = AgentState(agent_name="TestRoot")
        result = create_agent(state, task="scan dir", name="Scanner")
        agent_id = result["agent_id"]
        assert agent_id in _agent_graph["nodes"]
        node = _agent_graph["nodes"][agent_id]
        assert node["name"] == "Scanner"
        assert node["task"] == "scan dir"
        assert node["status"] == "running"

    def test_create_agent_adds_delegation_edge(self):
        _cleanup_graph()
        state = AgentState(agent_name="Parent")
        result = create_agent(state, task="child task", name="Child")
        delegation_edges = [e for e in _agent_graph["edges"] if e["type"] == "delegation"]
        assert any(e["from"] == state.agent_id and e["to"] == result["agent_id"]
                   for e in delegation_edges)

    def test_create_agent_no_inherit_context(self):
        _cleanup_graph()
        state = AgentState(agent_name="Parent")
        state.add_message("user", "secret context")
        result = create_agent(state, task="fresh task", name="FreshChild", inherit_context=False)
        # Child should have been created; we check it doesn't crash
        assert result["success"] is True


class TestSendMessage:
    def test_send_message_to_agent(self):
        _cleanup_graph()
        sender = AgentState(agent_name="Sender")
        receiver = AgentState(agent_name="Receiver")
        # Register receiver in graph
        _agent_graph["nodes"][receiver.agent_id] = {
            "id": receiver.agent_id, "name": "Receiver", "task": "wait",
            "status": "running", "parent_id": None, "created_at": "",
            "finished_at": None, "result": None, "role": "",
        }
        _agent_messages[receiver.agent_id] = []

        result = send_message_to_agent(sender, receiver.agent_id, "hello", "information", "normal")
        assert result["success"] is True
        assert result["delivery_status"] == "delivered"
        assert len(_agent_messages[receiver.agent_id]) == 1
        msg = _agent_messages[receiver.agent_id][0]
        assert msg["content"] == "hello"
        assert msg["from"] == sender.agent_id
        assert msg["read"] is False

    def test_send_message_unknown_target(self):
        _cleanup_graph()
        sender = AgentState(agent_name="Sender")
        result = send_message_to_agent(sender, "nonexistent", "hi")
        assert result["success"] is False


class TestWaitForMessage:
    def test_wait_for_message_sets_waiting(self):
        _cleanup_graph()
        state = AgentState(agent_name="Waiter")
        _agent_graph["nodes"][state.agent_id] = {
            "id": state.agent_id, "name": "Waiter", "task": "test",
            "status": "running", "parent_id": None, "created_at": "",
            "finished_at": None, "result": None, "role": "",
        }
        result = wait_for_message(state, reason="test wait")
        assert result["success"] is True
        assert result["status"] == "waiting"
        assert state.waiting_for_input is True
        assert _agent_graph["nodes"][state.agent_id]["status"] == "waiting"


class TestAgentFinish:
    def test_agent_finish_root_agent_rejected(self):
        _cleanup_graph()
        state = AgentState(agent_name="Root", parent_id=None)
        _agent_graph["nodes"][state.agent_id] = {
            "id": state.agent_id, "name": "Root", "task": "root task",
            "status": "running", "parent_id": None, "created_at": "",
            "finished_at": None, "result": None, "role": "",
        }
        result = agent_finish(state, result_summary="done")
        assert result["agent_completed"] is False
        assert "subagents" in result.get("error", "").lower()

    def test_agent_finish_notifies_parent(self):
        _cleanup_graph()
        parent = AgentState(agent_name="Parent")
        child = AgentState(agent_name="Child", parent_id=parent.agent_id)
        _agent_graph["nodes"][parent.agent_id] = {
            "id": parent.agent_id, "name": "Parent", "task": "parent task",
            "status": "running", "parent_id": None, "created_at": "",
            "finished_at": None, "result": None, "role": "",
        }
        _agent_graph["nodes"][child.agent_id] = {
            "id": child.agent_id, "name": "Child", "task": "child task",
            "status": "running", "parent_id": parent.agent_id, "created_at": "",
            "finished_at": None, "result": None, "role": "",
        }
        _agent_messages[parent.agent_id] = []

        result = agent_finish(child, result_summary="done", findings=["F-1"],
                             success=True, report_to_parent=True)
        assert result["agent_completed"] is True
        assert result["parent_notified"] is True
        assert len(_agent_messages[parent.agent_id]) == 1
        report = _agent_messages[parent.agent_id][0]["content"]
        assert "<agent_completion_report>" in report
        assert "done" in report
        assert _agent_graph["nodes"][child.agent_id]["status"] == "finished"

    def test_agent_finish_no_report_to_parent(self):
        _cleanup_graph()
        parent = AgentState(agent_name="Parent")
        child = AgentState(agent_name="Child", parent_id=parent.agent_id)
        _agent_graph["nodes"][parent.agent_id] = {
            "id": parent.agent_id, "name": "Parent", "task": "parent task",
            "status": "running", "parent_id": None, "created_at": "",
            "finished_at": None, "result": None, "role": "",
        }
        _agent_graph["nodes"][child.agent_id] = {
            "id": child.agent_id, "name": "Child", "task": "child task",
            "status": "running", "parent_id": parent.agent_id, "created_at": "",
            "finished_at": None, "result": None, "role": "",
        }
        _agent_messages[parent.agent_id] = []

        result = agent_finish(child, result_summary="silent done", report_to_parent=False)
        assert result["parent_notified"] is False
```

- [ ] **Step 2: 运行测试验证失败**

```bash
.venv/bin/pytest tests/test_graph_primitives.py -v
# Expected: FAIL (ImportError: create_agent, etc. not defined)
```

- [ ] **Step 3: 在 graph.py 中实现五个核心原语**

```python
# 追加到 src/nano_strix/agents/per_file_lib/graph.py

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

        # Construct <agent_delegation> XML for child
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

        # Build inherited context
        inherited_messages = []
        if inherit_context:
            inherited_messages = agent_state.get_conversation_history()

        # Register node
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

            # Add delegation edge
            delegation_edge = {
                "from": parent_id,
                "to": child_state.agent_id,
                "type": "delegation",
                "created_at": _now_iso(),
            }
            _agent_graph["edges"].append(delegation_edge)

            # Init inbox
            _agent_messages[child_state.agent_id] = []

        # Inject agent_delegation into child's messages
        child_state.add_message("user", delegation_xml)

        # Inject inherited context
        if inherit_context and inherited_messages:
            child_state.add_message("user", "<inherited_context_from_parent>")
            for msg in inherited_messages:
                child_state.add_message(msg["role"], msg["content"])
            child_state.add_message("user", "</inherited_context_from_parent>")

        # Start thread (deferred import to avoid circular dependency)
        from nano_strix.agents.per_file_lib.deep_agent import DeepAnalyseAgent
        agent = DeepAnalyseAgent(state=child_state)

        with _agent_graph_lock:
            _agent_instances[child_state.agent_id] = agent

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
        return {
            "success": True,
            "agent_id": child_state.agent_id,
            "message": f"Agent '{name}' created and started asynchronously",
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

        # Wake up target if waiting
        target_state = _agent_states.get(target_agent_id)
        if target_state is not None and target_state.waiting_for_input:
            target_state._wake_event.set()

        sender_name = _agent_graph["nodes"][sender_id]["name"]
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

                # Wake parent
                parent_state = _agent_states.get(parent_id)
                if parent_state is not None and parent_state.waiting_for_input:
                    parent_state._wake_event.set()

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

        # Find root
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

        # Summary stats
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
```

**注意**：`create_agent` 中延迟导入 `DeepAnalyseAgent` 以避免循环依赖。Task 9 实现 `DeepAnalyseAgent` 后此导入才能正常工作。

- [ ] **Step 4: 暂时跳过 create_agent 的测试，只运行其余四个原语的测试**

```bash
.venv/bin/pytest tests/test_graph_primitives.py -v -k "not TestCreateAgent"
# Expected: send_message / wait_for_message / agent_finish 全部 PASS
# TestCreateAgent 会失败（DeepAnalyseAgent 不存在），在 Task 9 后补测
```

- [ ] **Step 5: Commit**

```bash
git add src/nano_strix/agents/per_file_lib/graph.py tests/test_graph_primitives.py
git commit -m "feat: add five core graph primitives (create/send/wait/finish/view)"
```

---

### Task 3: Graph 工具的 XML Schema 文件

**Files:**
- Create: `src/nano_strix/agents/per_file_lib/graph_schema.xml`

- [ ] **Step 1: 创建 XML Schema**

```xml
<!-- src/nano_strix/agents/per_file_lib/graph_schema.xml -->
<tools>
  <tool name="create_agent">
    <description>Create and spawn a new agent to handle a specific subtask. The new agent runs on a separate daemon thread and inherits parent context by default.</description>
    <parameters>
      <parameter name="agent_state" type="string" required="true">
        <description>Internal agent state reference (injected automatically)</description>
      </parameter>
      <parameter name="task" type="string" required="true">
        <description>The specific task/objective for the new agent</description>
      </parameter>
      <parameter name="name" type="string" required="true">
        <description>Human-readable name for the agent</description>
      </parameter>
      <parameter name="inherit_context" type="boolean" required="false">
        <description>Whether to inherit parent's conversation history (default: true)</description>
      </parameter>
    </parameters>
  </tool>
  <tool name="send_message_to_agent">
    <description>Send a message to another agent for coordination.</description>
    <parameters>
      <parameter name="agent_state" type="string" required="true">
        <description>Internal agent state reference</description>
      </parameter>
      <parameter name="target_agent_id" type="string" required="true">
        <description>ID of the target agent</description>
      </parameter>
      <parameter name="message" type="string" required="true">
        <description>The message content</description>
      </parameter>
      <parameter name="message_type" type="string" required="false">
        <description>Type: "query", "instruction", or "information" (default: "information")</description>
      </parameter>
      <parameter name="priority" type="string" required="false">
        <description>Priority: "low", "normal", "high", or "urgent" (default: "normal")</description>
      </parameter>
    </parameters>
  </tool>
  <tool name="wait_for_message">
    <description>Pause the agent loop until receiving a message from another agent. IMPORTANT: Only use when sub-agents are actively running.</description>
    <parameters>
      <parameter name="agent_state" type="string" required="true">
        <description>Internal agent state reference</description>
      </parameter>
      <parameter name="reason" type="string" required="false">
        <description>Explanation for why the agent is waiting</description>
      </parameter>
    </parameters>
  </tool>
  <tool name="agent_finish">
    <description>Mark a subagent's task as completed and report results to parent agent. Can ONLY be used by subagents (agents with a parent).</description>
    <parameters>
      <parameter name="agent_state" type="string" required="true">
        <description>Internal agent state reference</description>
      </parameter>
      <parameter name="result_summary" type="string" required="true">
        <description>Summary of what was accomplished</description>
      </parameter>
      <parameter name="findings" type="string" required="false">
        <description>List of specific findings or discoveries</description>
      </parameter>
      <parameter name="success" type="boolean" required="false">
        <description>Whether the task completed successfully (default: true)</description>
      </parameter>
      <parameter name="report_to_parent" type="boolean" required="false">
        <description>Whether to send results back to parent (default: true)</description>
      </parameter>
      <parameter name="final_recommendations" type="string" required="false">
        <description>Recommendations for next steps</description>
      </parameter>
    </parameters>
  </tool>
  <tool name="view_agent_graph">
    <description>View the current agent graph showing all agents, their relationships, and status.</description>
    <parameters>
      <parameter name="agent_state" type="string" required="true">
        <description>Internal agent state reference</description>
      </parameter>
    </parameters>
  </tool>
</tools>
```

- [ ] **Step 2: Commit**

```bash
git add src/nano_strix/agents/per_file_lib/graph_schema.xml
git commit -m "feat: add XML tool schema for graph primitives"
```

---

### Task 4: Manifest 增强 (merge + 序列化)

**Files:**
- Modify: `src/nano_strix/agents/per_file_lib/manifest.py`
- Test: `tests/test_manifest_merge.py`

- [ ] **Step 1: 编写 merge 测试**

```python
# tests/test_manifest_merge.py
import json
from pathlib import Path
from nano_strix.agents.per_file_lib.manifest import FileManifest, ManifestFile


def _make_manifest(path: Path, files: dict) -> FileManifest:
    m = FileManifest(
        path=path, phase="analysis", files=files,
        agents_state={}, discovered_routes=[],
    )
    m._agent_names = []
    return m


def test_manifest_merge_new_files():
    parent = _make_manifest(Path("/tmp/m1.json"), {})
    child = _make_manifest(Path("/tmp/m2.json"), {
        "src/a.py": ManifestFile(priority="high", dimensions=["auth"], _path="src/a.py"),
    })
    parent.merge(child)
    assert "src/a.py" in parent.files
    assert parent.files["src/a.py"].priority == "high"


def test_manifest_merge_combines_findings():
    parent = _make_manifest(Path("/tmp/m1.json"), {
        "src/x.py": ManifestFile(priority="high", dimensions=["route"], _path="src/x.py"),
    })
    parent.files["src/x.py"].findings = [{"id": "F-1", "title": "old"}]

    child = _make_manifest(Path("/tmp/m2.json"), {
        "src/x.py": ManifestFile(priority="high", dimensions=["route"], _path="src/x.py"),
    })
    child.files["src/x.py"].findings = [{"id": "F-2", "title": "new"}]

    parent.merge(child)
    assert len(parent.files["src/x.py"].findings) == 2


def test_manifest_merge_combines_scan_findings():
    parent = _make_manifest(Path("/tmp/m1.json"), {
        "src/x.py": ManifestFile(priority="high", dimensions=[], _path="src/x.py"),
    })
    parent.files["src/x.py"].scan_findings = [{"rule": "r1"}]

    child = _make_manifest(Path("/tmp/m2.json"), {
        "src/x.py": ManifestFile(priority="high", dimensions=[], _path="src/x.py"),
    })
    child.files["src/x.py"].scan_findings = [{"rule": "r2"}]

    parent.merge(child)
    assert len(parent.files["src/x.py"].scan_findings) == 2


def test_manifest_merge_skip_votes():
    parent = _make_manifest(Path("/tmp/m1.json"), {
        "src/x.py": ManifestFile(priority="high", dimensions=[], _path="src/x.py"),
    })
    parent.files["src/x.py"].skip_votes = {"agent_a": "analyze"}

    child = _make_manifest(Path("/tmp/m2.json"), {
        "src/x.py": ManifestFile(priority="high", dimensions=[], _path="src/x.py"),
    })
    child.files["src/x.py"].skip_votes = {"agent_b": "skip"}

    parent.merge(child)
    assert parent.files["src/x.py"].skip_votes == {"agent_a": "analyze", "agent_b": "skip"}


def test_manifest_full_roundtrip():
    """to_dict() -> from_dict() -> to_dict() should be idempotent."""
    parent = _make_manifest(Path("/tmp/m1.json"), {
        "src/x.py": ManifestFile(priority="high", dimensions=["auth", "route"], _path="src/x.py"),
    })
    parent.files["src/x.py"].findings = [{"id": "F-1"}]
    parent.files["src/x.py"].scan_findings = [{"rule": "r1"}]
    parent.files["src/x.py"].skip_votes = {"a": "analyze"}
    parent.phase = "analysis"
    parent.discovered_routes = [{"path": "/api", "method": "GET"}]

    d = parent.to_dict()
    restored = FileManifest.from_dict(d)

    assert restored.phase == "analysis"
    assert "src/x.py" in restored.files
    assert restored.files["src/x.py"].priority == "high"
    assert len(restored.files["src/x.py"].findings) == 1
```

- [ ] **Step 2: 运行测试验证失败**

```bash
.venv/bin/pytest tests/test_manifest_merge.py -v
# Expected: FAIL (AttributeError: merge / to_dict / from_dict)
```

- [ ] **Step 3: 在 manifest.py 中增加 merge + to_dict/from_dict**

```python
# 追加到 FileManifest 类中

def to_dict(self) -> dict[str, Any]:
    with self._lock:
        return {
            "phase": self.phase,
            "max_file_retries": self.max_file_retries,
            "agents_state": dict(self.agents_state),
            "discovered_routes": list(self.discovered_routes),
            "files": {path: mf.to_dict() for path, mf in self._files.items()},
            "agent_names": list(self._agent_names),
        }

@classmethod
def from_dict(cls, data: dict[str, Any]) -> "FileManifest":
    files: dict[str, ManifestFile] = {}
    for file_path, fdata in data.get("files", {}).items():
        mf = ManifestFile.from_dict(fdata)
        mf._path = file_path
        files[file_path] = mf
    m = cls(
        path=Path("/tmp/dummy.json"),  # caller should set _path
        phase=data.get("phase", "analysis"),
        files=files,
        agents_state=data.get("agents_state", {}),
        discovered_routes=data.get("discovered_routes", []),
        max_file_retries=data.get("max_file_retries", 3),
    )
    m._agent_names = data.get("agent_names", list(m.agents_state.keys()))
    return m

def merge(self, other: "FileManifest") -> None:
    """Merge another manifest into this one. Merged files have combined findings, scan_findings, and skip_votes."""
    with self._lock:
        for path, other_file in other.files.items():
            if path not in self._files:
                self._files[path] = other_file
            else:
                existing = self._files[path]
                existing.findings.extend(other_file.findings)
                existing.scan_findings.extend(other_file.scan_findings)
                existing.skip_votes.update(other_file.skip_votes)
                # If other has a higher priority, use it
                prio_order = {"high": 0, "medium": 1, "low": 2}
                if prio_order.get(other_file.priority, 2) < prio_order.get(existing.priority, 2):
                    existing.priority = other_file.priority
                # Merge dimensions
                for dim in other_file.dimensions:
                    if dim not in existing.dimensions:
                        existing.dimensions.append(dim)
        self.discovered_routes.extend(other.discovered_routes)
        # Merge agents_state
        for name, state in other.agents_state.items():
            if name not in self.agents_state:
                self.agents_state[name] = state
            else:
                self.agents_state[name].update(state)
        # Rebuild agent_names
        self._agent_names = list(self.agents_state.keys())
```

- [ ] **Step 4: 运行测试验证通过**

```bash
.venv/bin/pytest tests/test_manifest_merge.py -v
# Expected: 全部 PASS
```

- [ ] **Step 5: Commit**

```bash
git add src/nano_strix/agents/per_file_lib/manifest.py tests/test_manifest_merge.py
git commit -m "feat: add merge, to_dict, from_dict to FileManifest"
```

---

### Task 5: OpenAICompatibleProvider

**Files:**
- Create: `src/nano_strix/llm/openai_compatible.py`
- Modify: `src/nano_strix/llm/__init__.py`
- Modify: `src/nano_strix/config/schema.py`
- Test: `tests/test_openai_provider.py`

- [ ] **Step 1: 查看现有 anthropic.py 的实现模式**

```bash
# 参考现有 AnthropicProvider 的实现
cat src/nano_strix/llm/anthropic.py
```

- [ ] **Step 2: 编写 OpenAICompatibleProvider 的测试**

```python
# tests/test_openai_provider.py
import pytest
from nano_strix.llm.openai_compatible import OpenAICompatibleProvider
from nano_strix.llm.adapter import LLMResponse, ToolCall
from nano_strix.config.schema import LLMConfig


def test_provider_creation():
    config = LLMConfig(
        provider="openai_compatible",
        api_key="test-key",
        base_url="https://api.openai.com/v1",
        model="gpt-4o",
    )
    provider = OpenAICompatibleProvider(config)
    assert provider is not None


def test_provider_tool_call_mapping():
    """Verify OpenAI tool call response maps to internal ToolCall."""
    provider = OpenAICompatibleProvider.__new__(OpenAICompatibleProvider)
    # Simulate a raw OpenAI tool call response
    raw_tool_call = type("obj", (object,), {
        "id": "call_123",
        "type": "function",
        "function": type("obj", (object,), {
            "name": "file_read",
            "arguments": '{"path": "/tmp/test.py"}',
        }),
    })()
    tc = provider._map_tool_call(raw_tool_call)
    assert tc.id == "call_123"
    assert tc.name == "file_read"
    assert tc.arguments == {"path": "/tmp/test.py"}
```

**注意**：完整 LLM 调用测试需要真实 API key，跳过集成测试。

- [ ] **Step 3: 实现 OpenAICompatibleProvider**

```python
# src/nano_strix/llm/openai_compatible.py
from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from typing import Any

from nano_strix.config.schema import LLMConfig
from nano_strix.llm.adapter import LLMProvider, LLMResponse, ToolCall
from nano_strix.llm.registry import register_provider

logger = logging.getLogger(__name__)


@register_provider("openai_compatible")
class OpenAICompatibleProvider(LLMProvider):
    def __init__(self, config: LLMConfig) -> None:
        self._config = config
        self._api_key = config.api_key
        self._base_url = config.base_url or "https://api.openai.com/v1"
        self._model = config.model or "gpt-4o"

        try:
            from openai import AsyncOpenAI
        except ImportError as e:
            raise ImportError(
                "openai package is required for OpenAICompatibleProvider. "
                "Install with: pip install openai"
            ) from e

        self._client = AsyncOpenAI(
            api_key=self._api_key,
            base_url=self._base_url,
        )

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        kwargs: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        if tools:
            openai_tools = []
            for t in tools:
                openai_tools.append({
                    "type": "function",
                    "function": {
                        "name": t["name"],
                        "description": t.get("description", ""),
                        "parameters": t.get("parameters", t.get("input_schema", {})),
                    },
                })
            kwargs["tools"] = openai_tools

        response = await self._client.chat.completions.create(**kwargs)
        choice = response.choices[0]
        msg = choice.message

        tool_calls = []
        if msg.tool_calls:
            for tc in msg.tool_calls:
                tool_calls.append(self._map_tool_call(tc))

        finish_map = {"stop": "stop", "tool_calls": "tool_calls", "length": "stop", "content_filter": "stop"}
        finish_reason = finish_map.get(choice.finish_reason or "stop", "stop")

        return LLMResponse(
            content=msg.content,
            tool_calls=tool_calls,
            finish_reason=finish_reason,
            usage={
                "input_tokens": response.usage.prompt_tokens if response.usage else 0,
                "output_tokens": response.usage.completion_tokens if response.usage else 0,
            },
            model=response.model,
        )

    async def stream_chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> AsyncIterator[str]:
        kwargs: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": True,
        }
        if tools:
            openai_tools = [{
                "type": "function",
                "function": {"name": t["name"], "description": t.get("description", ""), "parameters": t.get("parameters", t.get("input_schema", {}))},
            } for t in tools]
            kwargs["tools"] = openai_tools

        stream = await self._client.chat.completions.create(**kwargs)
        async for chunk in stream:
            delta = chunk.choices[0].delta if chunk.choices else None
            if delta and delta.content:
                yield delta.content

    def _map_tool_call(self, raw_tc: Any) -> ToolCall:
        try:
            args = json.loads(raw_tc.function.arguments)
        except (json.JSONDecodeError, AttributeError):
            args = {}
        return ToolCall(
            id=raw_tc.id if hasattr(raw_tc, "id") else "",
            name=raw_tc.function.name if hasattr(raw_tc.function, "name") else "",
            arguments=args,
        )
```

- [ ] **Step 4: 更新 LLM 模块的 __init__.py 导出**

```python
# 修改 src/nano_strix/llm/__init__.py，追加一行:
from nano_strix.llm.openai_compatible import OpenAICompatibleProvider  # noqa: F401
```

- [ ] **Step 5: 运行测试**

```bash
.venv/bin/pip install openai
.venv/bin/pytest tests/test_openai_provider.py -v
# Expected: 2 PASS
```

- [ ] **Step 6: Commit**

```bash
git add src/nano_strix/llm/openai_compatible.py src/nano_strix/llm/__init__.py tests/test_openai_provider.py
git commit -m "feat: add OpenAICompatibleProvider for multi-protocol LLM support"
```

---

### Task 6: DockerSandbox + Tool Server

**Files:**
- Modify: `src/nano_strix/sandbox/docker.py`
- Create: `src/nano_strix/sandbox/tool_server.py`
- Test: `tests/test_docker_sandbox.py`

- [ ] **Step 1: 编写 DockerSandbox 测试**

```python
# tests/test_docker_sandbox.py
import pytest
from nano_strix.sandbox.docker import DockerSandbox


@pytest.mark.skipif(not _docker_available(), reason="Docker not available")
class TestDockerSandbox:
    async def test_create_and_destroy(self):
        sb = DockerSandbox(image="nano-strix-sandbox:latest")
        await sb.create()
        assert sb._container is not None
        status = sb._container.status
        assert status == "running"
        await sb.destroy()

    async def test_execute_command(self):
        sb = DockerSandbox(image="nano-strix-sandbox:latest")
        await sb.create()
        result = await sb.execute("echo hello", timeout=10)
        assert result.exit_code == 0
        assert "hello" in result.stdout
        await sb.destroy()


def _docker_available():
    try:
        import docker
        client = docker.from_env()
        client.ping()
        return True
    except Exception:
        return False
```

- [ ] **Step 2: 实现 DockerSandbox**

```python
# src/nano_strix/sandbox/docker.py (替换现有骨架)
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from nano_strix.sandbox.base import ExecutionResult, Sandbox

logger = logging.getLogger(__name__)


class DockerSandbox(Sandbox):
    def __init__(
        self,
        image: str = "nano-strix-sandbox:latest",
        network: str = "none",
        source_dir: Path | None = None,
        tool_server_port: int = 8080,
    ) -> None:
        self._image = image
        self._network = network
        self._source_dir = source_dir
        self._tool_server_port = tool_server_port
        self._container: Any = None
        self._client: Any = None
        self._tool_server_url: str = ""

    async def create(self) -> None:
        try:
            import docker
        except ImportError:
            raise ImportError("docker package required: pip install docker")

        self._client = docker.from_env()
        volumes = {}
        if self._source_dir:
            volumes[str(self._source_dir)] = {
                "bind": "/workspace/source",
                "mode": "ro",
            }

        self._container = self._client.containers.run(
            self._image,
            command=["python", "-m", "nano_strix.sandbox.tool_server"],
            network=self._network,
            volumes=volumes,
            ports={"8080/tcp": self._tool_server_port},
            detach=True,
            remove=True,
        )

        import time
        time.sleep(1)  # Wait for tool server to start
        self._tool_server_url = f"http://localhost:{self._tool_server_port}"

    async def destroy(self) -> None:
        if self._container:
            try:
                self._container.stop(timeout=5)
            except Exception:
                pass
            self._container = None

    async def execute(self, command: str, timeout: int = 30) -> ExecutionResult:
        """Execute a command in the sandbox via tool server API."""
        import aiohttp
        import asyncio
        import time

        start = time.monotonic()
        try:
            timeout_obj = aiohttp.ClientTimeout(total=timeout + 5)
            async with aiohttp.ClientSession(timeout=timeout_obj) as session:
                async with session.post(
                    f"{self._tool_server_url}/tools/terminal_execute",
                    json={"command": command, "timeout": timeout},
                ) as resp:
                    data = await resp.json()
                    return ExecutionResult(
                        exit_code=data.get("exit_code", -1),
                        stdout=data.get("stdout", ""),
                        stderr=data.get("stderr", ""),
                        duration=time.monotonic() - start,
                    )
        except Exception as e:
            return ExecutionResult(
                exit_code=-1,
                stdout="",
                stderr=str(e),
                duration=time.monotonic() - start,
            )

    async def copy_in(self, local_path: str, sandbox_path: str) -> None:
        if self._container:
            import tarfile
            import io
            tar_stream = io.BytesIO()
            with tarfile.open(fileobj=tar_stream, mode="w") as tar:
                tar.add(local_path, arcname=Path(sandbox_path).name)
            tar_stream.seek(0)
            self._container.put_archive(str(Path(sandbox_path).parent), tar_stream)

    async def copy_out(self, sandbox_path: str, local_path: str) -> None:
        if self._container:
            bits, _ = self._container.get_archive(sandbox_path)
            import tarfile
            import io
            tar_stream = io.BytesIO()
            for chunk in bits:
                tar_stream.write(chunk)
            tar_stream.seek(0)
            with tarfile.open(fileobj=tar_stream, mode="r") as tar:
                tar.extractall(Path(local_path).parent)

    async def call_tool_server(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Call a specific tool on the sandbox tool server."""
        import aiohttp

        endpoint_map = {
            "semgrep": "/tools/scanner/semgrep",
            "bandit": "/tools/scanner/bandit",
            "file_read": "/tools/file_read",
            "terminal_execute": "/tools/terminal_execute",
        }
        endpoint = endpoint_map.get(tool_name, f"/tools/{tool_name}")

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self._tool_server_url}{endpoint}",
                    json=arguments,
                    timeout=aiohttp.ClientTimeout(total=120),
                ) as resp:
                    return await resp.json()
        except Exception as e:
            return {"error": str(e)}
```

- [ ] **Step 3: 实现 Tool Server（容器内 Flask 应用）**

```python
# src/nano_strix/sandbox/tool_server.py
"""Lightweight HTTP tool server running inside Docker sandbox."""

import json
import subprocess
import sys
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path


class ToolHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length) if content_length else b"{}"
        try:
            args = json.loads(body)
        except json.JSONDecodeError:
            args = {}

        if self.path == "/tools/terminal_execute":
            result = self._handle_terminal(args)
        elif self.path == "/tools/file_read":
            result = self._handle_file_read(args)
        elif self.path == "/tools/scanner/semgrep":
            result = self._handle_semgrep(args)
        elif self.path == "/tools/scanner/bandit":
            result = self._handle_bandit(args)
        else:
            result = {"error": f"Unknown tool: {self.path}"}

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(result).encode())

    def _handle_terminal(self, args: dict) -> dict:
        command = args.get("command", "")
        timeout = args.get("timeout", 30)
        try:
            proc = subprocess.run(
                command, shell=True, capture_output=True, text=True,
                timeout=timeout, cwd="/workspace/source",
            )
            return {"exit_code": proc.returncode, "stdout": proc.stdout, "stderr": proc.stderr}
        except subprocess.TimeoutExpired:
            return {"exit_code": -1, "stdout": "", "stderr": "Command timed out"}

    def _handle_file_read(self, args: dict) -> dict:
        path = args.get("path", "")
        full_path = Path("/workspace/source") / path
        try:
            content = full_path.read_text(errors="replace")
            return {"content": content, "size": len(content)}
        except Exception as e:
            return {"error": str(e)}

    def _handle_semgrep(self, args: dict) -> dict:
        target = args.get("target", "/workspace/source")
        try:
            proc = subprocess.run(
                ["semgrep", "--config", "auto", "--json", target],
                capture_output=True, text=True, timeout=120,
            )
            return {"exit_code": proc.returncode, "stdout": proc.stdout, "stderr": proc.stderr}
        except FileNotFoundError:
            return {"error": "semgrep not installed in sandbox"}
        except subprocess.TimeoutExpired:
            return {"error": "semgrep timed out"}

    def _handle_bandit(self, args: dict) -> dict:
        target = args.get("target", "/workspace/source")
        try:
            proc = subprocess.run(
                ["bandit", "-r", "-f", "json", target],
                capture_output=True, text=True, timeout=120,
            )
            return {"exit_code": proc.returncode, "stdout": proc.stdout, "stderr": proc.stderr}
        except FileNotFoundError:
            return {"error": "bandit not installed in sandbox"}
        except subprocess.TimeoutExpired:
            return {"error": "bandit timed out"}

    def log_message(self, format, *args):
        pass  # Suppress HTTP request logging


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8080
    server = HTTPServer(("0.0.0.0", port), ToolHandler)
    print(f"Tool server listening on port {port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Commit**

```bash
git add src/nano_strix/sandbox/docker.py src/nano_strix/sandbox/tool_server.py tests/test_docker_sandbox.py
git commit -m "feat: implement DockerSandbox with tool server for static scanning"
```

---

### Task 7: Skills 技能加载系统

**Files:**
- Create: `src/nano_strix/skills/__init__.py`
- Create: `src/nano_strix/skills/loader.py`
- Create: `src/nano_strix/skills/sql_injection.md`
- Create: `src/nano_strix/skills/xss.md`
- Create: `src/nano_strix/skills/auth_jwt.md`
- Create: `src/nano_strix/skills/ssrf.md`
- Create: `src/nano_strix/skills/rce.md`
- Test: `tests/test_skills.py`

- [ ] **Step 1: 编写 skills 测试**

```python
# tests/test_skills.py
from pathlib import Path
import tempfile
from nano_strix.skills.loader import SkillLoader


def test_skill_loader_loads_markdown_files():
    with tempfile.TemporaryDirectory() as tmpdir:
        skills_dir = Path(tmpdir)
        (skills_dir / "test_skill.md").write_text("# Test Skill\nTest content.")
        (skills_dir / "other.md").write_text("# Other\nOther content.")

        loader = SkillLoader(skills_dir)
        loader.load_all()

        assert "test_skill" in loader.list_skills()
        assert "other" in loader.list_skills()
        assert "Test content" in loader.get_skill("test_skill")


def test_skill_loader_empty_dir():
    with tempfile.TemporaryDirectory() as tmpdir:
        loader = SkillLoader(Path(tmpdir))
        loader.load_all()
        assert loader.list_skills() == []


def test_skill_loader_get_nonexistent():
    with tempfile.TemporaryDirectory() as tmpdir:
        loader = SkillLoader(Path(tmpdir))
        loader.load_all()
        result = loader.get_skill("nonexistent")
        assert result == ""


def test_load_skill_tool():
    from nano_strix.skills.loader import load_skill, _skill_loader
    from nano_strix.agents.per_file_lib.graph import AgentState
    import tempfile

    with tempfile.TemporaryDirectory() as tmpdir:
        skills_dir = Path(tmpdir)
        (skills_dir / "sql_injection.md").write_text("# SQL Injection\nTest injection guide.")
        loader = SkillLoader(skills_dir)
        loader.load_all()

        # Temporarily set the global loader
        import nano_strix.skills.loader as sk_mod
        sk_mod._skill_loader = loader

        state = AgentState(agent_name="TestAgent")
        result = load_skill(state, "sql_injection")
        assert result["success"] is True
        assert result["skill"] == "sql_injection"
        # Agent should have received the skill content as a message
        assert any("<specialized_knowledge>" in m["content"] for m in state.messages)
```

- [ ] **Step 2: 实现 SkillLoader**

```python
# src/nano_strix/skills/__init__.py
from nano_strix.skills.loader import SkillLoader, load_skill  # noqa: F401


# src/nano_strix/skills/loader.py
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_skill_loader: SkillLoader | None = None


class SkillLoader:
    def __init__(self, skills_dir: Path) -> None:
        self._skills_dir = skills_dir
        self._skills: dict[str, str] = {}

    def load_all(self) -> dict[str, str]:
        if not self._skills_dir.exists():
            logger.warning("Skills directory not found: %s", self._skills_dir)
            return {}
        for md_file in self._skills_dir.glob("*.md"):
            skill_name = md_file.stem
            content = md_file.read_text(errors="replace")
            self._skills[skill_name] = content
            logger.debug("Loaded skill: %s (%d chars)", skill_name, len(content))
        return dict(self._skills)

    def get_skill(self, name: str) -> str:
        return self._skills.get(name, "")

    def list_skills(self) -> list[str]:
        return sorted(self._skills.keys())


def load_skill(agent_state: Any, skill_name: str) -> dict[str, Any]:
    """Load a vulnerability-specific skill guide into the agent's context.

    Exposed as a @register_tool for LLM agents.
    """
    global _skill_loader
    if _skill_loader is None:
        return {"success": False, "error": "SkillLoader not initialized"}
    content = _skill_loader.get_skill(skill_name)
    if not content:
        return {"success": False, "error": f"Unknown skill: {skill_name}"}
    agent_state.add_message(
        "user",
        f"<specialized_knowledge name=\"{skill_name}\">\n{content}\n</specialized_knowledge>",
    )
    return {"success": True, "skill": skill_name, "size_chars": len(content)}


def set_skill_loader(loader: SkillLoader) -> None:
    global _skill_loader
    _skill_loader = loader
```

- [ ] **Step 3: 创建技能知识文件**

```markdown
<!-- src/nano_strix/skills/sql_injection.md -->
# SQL Injection Analysis Guide

## Detection
- Look for string concatenation in SQL queries
- Check for f-strings used with database queries
- Identify unescaped user input in WHERE clauses
- Watch for dynamic table/column names from user input

## Common Patterns
- cursor.execute(f"SELECT * FROM users WHERE name='{user}'")
- query = "SELECT * FROM " + table_name
- raw SQL strings passed to ORM methods

## Recommendations
- Use parameterized queries (?, %s placeholders)
- Use ORM safely (no raw SQL unless necessary)
- Validate and sanitize all user input
```

```markdown
<!-- src/nano_strix/skills/xss.md -->
# Cross-Site Scripting (XSS) Analysis Guide

## Detection
- Check for unsanitized user input rendered in HTML templates
- Look for innerHTML, document.write, eval usage
- Identify missing Content-Security-Policy headers

## Common Patterns
- template.render(user_input=request.args.get('q'))
- return f"<div>{user_name}</div>"
- <script>var data = {{ user_json | safe }};</script>

## Recommendations
- Always escape output (HTML entity encoding)
- Use template auto-escaping
- Set Content-Security-Policy headers
```

```markdown
<!-- src/nano_strix/skills/auth_jwt.md -->
# Authentication & JWT Analysis Guide

## Detection
- Check JWT signature verification (alg:none attacks)
- Look for hardcoded secrets/keys
- Check session token generation randomness
- Verify password hashing strength (bcrypt, argon2)

## Common Patterns
- jwt.decode(token, verify=False)
- SECRET_KEY = "hardcoded-secret"
- hashlib.md5(password).hexdigest()

## Recommendations
- Always verify JWT signatures
- Use strong secret keys from env vars
- Use bcrypt/argon2 for password hashing
```

```markdown
<!-- src/nano_strix/skills/ssrf.md -->
# SSRF Analysis Guide

## Detection
- Check for user-controlled URLs in HTTP requests
- Look for requests made to internal IP ranges
- Identify URL fetching without validation

## Common Patterns
- requests.get(user_provided_url)
- urllib.request.urlopen(input_url)
- curl_exec(user_url)

## Recommendations
- Whitelist allowed domains/IPs
- Block requests to internal networks
- Use URL parsing to validate scheme and host
```

```markdown
<!-- src/nano_strix/skills/rce.md -->
# Remote Code Execution Analysis Guide

## Detection
- Check for eval, exec, compile usage with user input
- Look for os.system, subprocess with shell=True
- Identify pickle/deserialization of user input
- Check template injection (SSTI)

## Common Patterns
- eval(user_input)
- os.system(f"ping {user_host}")
- pickle.loads(user_data)
- subprocess.run(user_cmd, shell=True)

## Recommendations
- Never eval user input
- Use subprocess with shell=False and argument lists
- Use safe serialization (JSON instead of pickle)
```

- [ ] **Step 4: 运行测试**

```bash
.venv/bin/pytest tests/test_skills.py -v
# Expected: 4 PASS
```

- [ ] **Step 5: Commit**

```bash
git add src/nano_strix/skills/ tests/test_skills.py
git commit -m "feat: add skills loading system with vulnerability knowledge files"
```

---

### Task 8: Prompt 模板

**Files:**
- Create: `src/nano_strix/agents/per_file_lib/prompts.py`
- Test: `tests/test_prompts.py`

- [ ] **Step 1: 编写测试**

```python
# tests/test_prompts.py
from nano_strix.agents.per_file_lib.prompts import (
    ROLE_TEMPLATE,
    ROLE_DEFINITIONS,
    build_system_prompt,
    build_user_prompt_for_file,
)


def test_build_root_system_prompt():
    prompt = build_system_prompt("root")
    assert "root orchestrator" in prompt.lower()
    assert "create_agent" in prompt
    assert "agent_finish" in prompt


def test_build_classify_system_prompt():
    prompt = build_system_prompt("classify")
    assert "classify" in prompt.lower()
    assert "file_search" in prompt


def test_build_analyze_system_prompt():
    prompt = build_system_prompt("analyze")
    assert "analyze" in prompt.lower()
    assert "load_skill" in prompt


def test_all_roles_have_prompts():
    for role in ROLE_DEFINITIONS:
        prompt = build_system_prompt(role)
        assert len(prompt) > 100
        assert ROLE_DEFINITIONS[role]["name"] in prompt


def test_build_user_prompt_includes_file_content():
    prompt = build_user_prompt_for_file(
        file_path="src/login.py",
        priority="high",
        content="def login(): pass",
        scan_findings=[],
        hints={},
    )
    assert "src/login.py" in prompt
    assert "high" in prompt
    assert "def login(): pass" in prompt
    assert "findings" in prompt.lower()
```

- [ ] **Step 2: 实现 prompts.py**

```python
# src/nano_strix/agents/per_file_lib/prompts.py
from __future__ import annotations

from string import Template
from typing import Any

ROLE_DEFINITIONS: dict[str, dict[str, str]] = {
    "root": {
        "name": "Root Orchestrator",
        "description": (
            "You are the root orchestrator agent for deep analysis. "
            "Your job is to coordinate the analysis pipeline across 5 phases:\n"
            "1. Classify - classify files by priority and dimensions\n"
            "2. Scan - run static analysis tools via Docker sandbox\n"
            "3. Analyze - per-file deep vulnerability analysis\n"
            "4. CrossLink - cross-file correlation analysis\n"
            "5. Review - deduplicate, cross-validate, and refine all findings\n\n"
            "For each phase, spawn a specialized sub-agent via create_agent. "
            "Wait for completion, then merge results and proceed to the next phase. "
            "Use check_coverage to verify all files are processed."
        ),
        "capabilities": "Phase orchestration, sub-agent coordination, manifest coverage tracking, result merging",
    },
    "classify": {
        "name": "File Classifier",
        "description": (
            "You classify source files by priority (high/medium/low) and dimensions "
            "(route/dataflow/auth/dependency). High priority: auth, API, input handling, "
            "command execution. Medium: business logic, middleware. Low: config, utils, tests."
        ),
        "capabilities": "File discovery, priority classification, dimension tagging",
    },
    "scan": {
        "name": "Static Scanner",
        "description": (
            "Run static analysis tools on the target codebase via Docker sandbox. "
            "Use semgrep for multi-language pattern scanning and bandit for Python security. "
            "Attach scan findings to the per-file manifest."
        ),
        "capabilities": "Static analysis tool execution, Docker sandbox integration",
    },
    "analyze": {
        "name": "Per-File Analyzer",
        "description": (
            "Deep analysis of individual source files. Read each file, apply domain "
            "knowledge (route/dataflow/auth/dependency), and identify security vulnerabilities. "
            "Use load_skill to get specialized guidance. If the workload is large, spawn "
            "sub-agents to parallelize."
        ),
        "capabilities": "Code review, vulnerability detection, pattern matching, skill-guided analysis",
    },
    "cross-link": {
        "name": "Cross-Link Analyzer",
        "description": (
            "Correlate findings across multiple files. Trace attack paths that span "
            "multiple components. Connect routes to dataflows, auth bypasses to sensitive "
            "endpoints. Identify chained vulnerabilities."
        ),
        "capabilities": "Cross-file correlation, attack path construction, chained vulnerability detection",
    },
    "review": {
        "name": "Review & Refine",
        "description": (
            "Review all findings from previous phases. Deduplicate similar findings. "
            "Cross-validate findings against source code. Eliminate false positives. "
            "Ensure finding quality and consistency. Produce final refined finding list."
        ),
        "capabilities": "Finding deduplication, false positive elimination, quality assurance, severity calibration",
    },
}

_COMMON_TEMPLATE = Template("""You are $role_name, a specialized security analysis agent.
Your task domain: $role_description

<core_capabilities>
$capabilities
</core_capabilities>

<communication_rules>
- Work autonomously on your assigned task
- Use agent_finish when complete to report back to parent
- NEVER send empty messages — use wait_for_message if idle
- You are a SPECIALIST — focus exclusively on your delegated task
</communication_rules>

<agent_graph_tools>
These tools let you coordinate with other agents:
- create_agent: spawn sub-agents for parallel work
- send_message_to_agent: communicate with sibling agents
- wait_for_message: pause until sub-agents complete
- agent_finish: report results to your parent
- view_agent_graph: view current agent tree structure
</agent_graph_tools>

<analysis_tools>
$tool_descriptions
</analysis_tools>

<output_format>
Return findings as a JSON object with a 'findings' array. Each finding:
{id, title, severity (critical/high/medium/low/info), category, file_path,
line_range [start, end], description, code_snippet, recommendation, confidence (0-1)}

If no issues found, return an empty findings list.
</output_format>""")

_TOOL_SETS: dict[str, str] = {
    "root": "- create_agent, wait_for_message, view_agent_graph, read_manifest, check_coverage, merge_manifest",
    "classify": "- file_search, file_read, directory_list, create_agent, agent_finish",
    "scan": "- tool_server_execute (semgrep/bandit via Docker sandbox), create_agent, agent_finish",
    "analyze": "- file_read, file_search, directory_list, load_skill, create_agent, agent_finish",
    "cross-link": "- file_read, file_search, load_skill, read_manifest, create_agent, agent_finish",
    "review": "- read_manifest, file_read, load_skill, create_agent, agent_finish",
}


def build_system_prompt(role: str) -> str:
    rd = ROLE_DEFINITIONS[role]
    return _COMMON_TEMPLATE.substitute(
        role_name=rd["name"],
        role_description=rd["description"],
        capabilities=rd["capabilities"],
        tool_descriptions=_TOOL_SETS.get(role, ""),
    )


def build_user_prompt_for_file(
    file_path: str,
    priority: str,
    content: str,
    scan_findings: list[dict[str, Any]],
    hints: dict[str, Any],
    max_content_len: int = 8000,
) -> str:
    if len(content) > max_content_len:
        content = content[:max_content_len] + "\n... [truncated]"

    hint_text = ""
    if hints.get("discovered_routes"):
        hint_text = "\nDiscovered routes:\n" + "\n".join(
            f"  {r['method']} {r['path']} ({r.get('file', '')}:{r.get('line', '')})"
            for r in hints["discovered_routes"]
        )

    return (
        f"File: {file_path}\n"
        f"Priority: {priority}\n"
        f"Static scan findings: {scan_findings}\n"
        f"{hint_text}\n\n"
        f"Source code:\n```\n{content}\n```\n\n"
        "Return a JSON object with a 'findings' list."
    )
```

- [ ] **Step 3: 运行测试**

```bash
.venv/bin/pytest tests/test_prompts.py -v
# Expected: 全部 PASS
```

- [ ] **Step 4: Commit**

```bash
git add src/nano_strix/agents/per_file_lib/prompts.py tests/test_prompts.py
git commit -m "feat: add unified prompt templates with role definitions"
```

---

### Task 9: DeepAnalyseAgent 基类

**Files:**
- Create: `src/nano_strix/agents/per_file_lib/deep_agent.py`
- Test: `tests/test_deep_agent.py`

- [ ] **Step 1: 编写 DeepAnalyseAgent 测试**

```python
# tests/test_deep_agent.py
import pytest
from nano_strix.agents.per_file_lib.deep_agent import DeepAnalyseAgent
from nano_strix.agents.per_file_lib.graph import AgentState


class FakeLLM:
    """Fake LLM provider for testing agent loop."""
    def __init__(self, responses=None):
        self.responses = responses or []
        self.call_count = 0

    async def chat(self, messages, tools=None, temperature=0.7, max_tokens=4096):
        from nano_strix.llm.adapter import LLMResponse
        if self.call_count < len(self.responses):
            resp = self.responses[self.call_count]
            self.call_count += 1
            return resp
        return LLMResponse(content='{"findings": []}', finish_reason="stop")


def test_deep_agent_creation():
    state = AgentState(agent_name="TestAgent", task="test task", role="analyze")
    agent = DeepAnalyseAgent(state=state)
    assert agent.state.agent_name == "TestAgent"
    assert agent.state.task == "test task"


def test_check_agent_messages_resumes_waiting():
    from nano_strix.agents.per_file_lib.graph import _agent_messages, _agent_graph
    # Clean up
    from nano_strix.agents.per_file_lib import graph
    graph._agent_graph["nodes"].clear()
    graph._agent_messages.clear()

    state = AgentState(agent_name="TestAgent", task="test")
    state.enter_waiting_state()
    assert state.waiting_for_input is True

    _agent_graph["nodes"][state.agent_id] = {
        "id": state.agent_id, "name": "TestAgent", "task": "test",
        "status": "waiting", "parent_id": None, "created_at": "",
        "finished_at": None, "result": None, "role": "",
    }
    _agent_messages[state.agent_id] = [{
        "id": "msg_1", "from": "agent_xxx", "to": state.agent_id,
        "content": "<agent_completion_report><summary>done</summary></agent_completion_report>",
        "message_type": "information", "priority": "high", "timestamp": "",
        "delivered": True, "read": False,
    }]

    agent = DeepAnalyseAgent(state=state)
    agent._check_agent_messages()
    # Should have resumed from waiting
    assert state.waiting_for_input is False
```

- [ ] **Step 2: 实现 DeepAnalyseAgent 基类**

```python
# src/nano_strix/agents/per_file_lib/deep_agent.py
from __future__ import annotations

import asyncio
import logging
from typing import Any

from nano_strix.agents.per_file_lib.graph import (
    AgentState,
    _agent_graph,
    _agent_messages,
    _agent_states,
    _agent_instances,
    _agent_graph_lock,
    _root_agent_id,
    _now_iso,
)
from nano_strix.agents.per_file_lib.prompts import build_system_prompt

logger = logging.getLogger(__name__)


class DeepAnalyseAgent:
    """Base agent class for deep analysis. Runs on a daemon thread with its own asyncio event loop."""

    max_iterations: int = 300

    def __init__(self, state: AgentState, llm_provider: Any = None) -> None:
        self.state = state
        self._llm = llm_provider
        self._system_prompt = build_system_prompt(state.role)
        self._register_in_graph()

    def _register_in_graph(self) -> None:
        if self.state.agent_id not in _agent_graph["nodes"]:
            _agent_graph["nodes"][self.state.agent_id] = {
                "id": self.state.agent_id,
                "name": self.state.agent_name,
                "task": self.state.task,
                "status": "running",
                "parent_id": self.state.parent_id,
                "role": self.state.role,
                "created_at": _now_iso(),
                "finished_at": None,
                "result": None,
            }

        with _agent_graph_lock:
            _agent_instances[self.state.agent_id] = self
            _agent_states[self.state.agent_id] = self.state

        if self.state.parent_id:
            _agent_graph["edges"].append({
                "from": self.state.parent_id,
                "to": self.state.agent_id,
                "type": "delegation",
                "created_at": _now_iso(),
            })

        if self.state.agent_id not in _agent_messages:
            _agent_messages[self.state.agent_id] = []

        if self.state.parent_id is None and _root_agent_id is None:
            import nano_strix.agents.per_file_lib.graph as g
            g._root_agent_id = self.state.agent_id

    def set_llm_provider(self, provider: Any) -> None:
        self._llm = provider

    async def agent_loop(self) -> dict[str, Any]:
        if self._llm is None:
            raise RuntimeError("LLM provider not set on agent")

        while True:
            self._check_agent_messages()

            if self.state.waiting_for_input:
                if self.state.has_waiting_timeout():
                    self.state.resume_from_waiting()
                    self.state.add_message("user", "Waiting timeout reached. Resuming.")
                    if self.state.agent_id in _agent_graph["nodes"]:
                        _agent_graph["nodes"][self.state.agent_id]["status"] = "running"
                else:
                    await self.state.wait_for_wake(timeout=0.5)
                    continue

            if self.state.should_stop():
                return self.state.final_result or {}

            self.state.increment_iteration()

            try:
                should_finish = await self._process_iteration()
                if should_finish:
                    return self.state.final_result or {"success": True}
            except Exception as e:
                logger.exception("Error in agent %s iteration %d", self.state.agent_name, self.state.iteration)
                if self.state.agent_id in _agent_graph["nodes"]:
                    _agent_graph["nodes"][self.state.agent_id]["status"] = "error"
                raise

    async def _process_iteration(self) -> bool:
        from nano_strix.tools.executor import execute_tool_with_validation
        from nano_strix.tools.registry import get_tool_by_name

        messages = [{"role": "system", "content": self._system_prompt}] + self.state.get_conversation_history()

        response = await self._llm.chat(
            messages=messages,
            tools=self._get_tools(),
            temperature=0.1,
            max_tokens=4096,
        )

        content = (response.content or "").strip()
        if not content and not response.has_tool_calls:
            self.state.add_message(
                "user",
                "You MUST NOT respond with empty messages. Use a tool or agent_finish."
            )
            return False

        self.state.add_message("assistant", content or "[tool call]")

        if response.has_tool_calls:
            should_finish = False
            for tc in response.tool_calls:
                tool_fn = get_tool_by_name(tc.name)
                try:
                    result = await execute_tool_with_validation(tc.name, tc.arguments, self.state)
                except Exception as e:
                    result = {"error": str(e)}
                self.state.add_message("user", f"Tool result ({tc.name}): {str(result)[:2000]}")

                if tc.name == "agent_finish":
                    should_finish = True

            return should_finish

        return False

    def _check_agent_messages(self) -> None:
        agent_id = self.state.agent_id
        if agent_id not in _agent_messages:
            return

        msgs = _agent_messages.get(agent_id, [])
        has_new = False
        for msg in msgs:
            if msg.get("read", False):
                continue

            sender_id = msg.get("from", "")
            if self.state.waiting_for_input:
                self.state.resume_from_waiting()
                has_new = True
                if agent_id in _agent_graph["nodes"]:
                    _agent_graph["nodes"][agent_id]["status"] = "running"

            if sender_id and sender_id in _agent_graph.get("nodes", {}):
                sender_name = _agent_graph["nodes"][sender_id]["name"]
            else:
                sender_name = sender_id or "unknown"

            formatted = f"""<inter_agent_message>
    <sender>
        <agent_name>{sender_name}</agent_name>
        <agent_id>{sender_id}</agent_id>
    </sender>
    <message_metadata>
        <type>{msg.get("message_type", "information")}</type>
        <priority>{msg.get("priority", "normal")}</priority>
    </message_metadata>
    <content>
{msg.get("content", "")}
    </content>
</inter_agent_message>"""
            self.state.add_message("user", formatted.strip())
            msg["read"] = True

        if has_new and not self.state.waiting_for_input:
            if agent_id in _agent_graph["nodes"]:
                _agent_graph["nodes"][agent_id]["status"] = "running"

    def _get_tools(self) -> list[dict[str, Any]]:
        from nano_strix.tools.registry import tools as registered_tools
        # Build Anthropic-compatible tool format from registered tools
        result = []
        for td in registered_tools:
            schema = {}
            from nano_strix.tools.registry import get_tool_param_schema
            param_schema = get_tool_param_schema(td["name"])
            if param_schema:
                schema["input_schema"] = param_schema
            result.append({
                "name": td["name"],
                "description": td.get("description", ""),
                **schema,
            })
        return result
```

- [ ] **Step 3: 运行测试**

```bash
.venv/bin/pytest tests/test_deep_agent.py -v
# Expected: PASS
```

- [ ] **Step 4: 验证 Task 2 中 create_agent 的测试现在也通过了**

```bash
.venv/bin/pytest tests/test_graph_primitives.py::TestCreateAgent -v
# Expected: 3 PASS
```

- [ ] **Step 5: Commit**

```bash
git add src/nano_strix/agents/per_file_lib/deep_agent.py tests/test_deep_agent.py
git commit -m "feat: add DeepAnalyseAgent base class with agent_loop"
```

---

### Task 10: 六种 Agent 子类

**Files:**
- Modify: `src/nano_strix/agents/per_file_lib/deep_agent.py` (追加子类)
- Test: `tests/test_agent_subclasses.py`

- [ ] **Step 1: 编写子类测试**

```python
# tests/test_agent_subclasses.py
from nano_strix.agents.per_file_lib.deep_agent import (
    DeepAnalyseAgent, RootAgent, ClassifyAgent, ScanAgent,
    AnalyzeAgent, CrossLinkAgent, ReviewAgent,
)
from nano_strix.agents.per_file_lib.graph import AgentState


def test_root_agent_role():
    state = AgentState(agent_name="Root", task="orchestrate")
    agent = RootAgent(state=state)
    assert agent.state.role == "root"
    assert "orchestrator" in agent._system_prompt.lower()


def test_classify_agent_role():
    state = AgentState(agent_name="Classifier", task="classify files")
    agent = ClassifyAgent(state=state)
    assert agent.state.role == "classify"


def test_scan_agent_role():
    state = AgentState(agent_name="Scanner", task="scan files")
    agent = ScanAgent(state=state)
    assert agent.state.role == "scan"


def test_analyze_agent_role():
    state = AgentState(agent_name="Analyzer", task="analyze login.py")
    agent = AnalyzeAgent(state=state)
    assert agent.state.role == "analyze"


def test_cross_link_agent_role():
    state = AgentState(agent_name="CrossLinker", task="cross-link")
    agent = CrossLinkAgent(state=state)
    assert agent.state.role == "cross-link"


def test_review_agent_role():
    state = AgentState(agent_name="Reviewer", task="review findings")
    agent = ReviewAgent(state=state)
    assert agent.state.role == "review"


def test_analyze_agent_should_split():
    state = AgentState(agent_name="Analyzer", task="analyze 500 files")
    agent = AnalyzeAgent(state=state)
    # 500 files > default threshold of 50, so should recommend split
    assert agent._should_split(file_count=500) is True


def test_analyze_agent_should_not_split_small_count():
    state = AgentState(agent_name="Analyzer", task="analyze 10 files")
    agent = AnalyzeAgent(state=state)
    assert agent._should_split(file_count=10) is False


def test_classify_agent_should_split():
    state = AgentState(agent_name="Classifier", task="classify 200 files")
    agent = ClassifyAgent(state=state)
    assert agent._should_split(file_count=200) is True
```

- [ ] **Step 2: 实现六种子类**

```python
# 追加到 src/nano_strix/agents/per_file_lib/deep_agent.py

class RootAgent(DeepAnalyseAgent):
    """Root orchestrator: schedules phases, manages manifest coverage."""

    def __init__(self, state: AgentState, llm_provider: Any = None) -> None:
        state.role = "root"
        super().__init__(state, llm_provider)
        self._split_threshold: int = 50


class ClassifyAgent(DeepAnalyseAgent):
    """Phase 1: File classification by priority and dimensions."""

    def __init__(self, state: AgentState, llm_provider: Any = None) -> None:
        state.role = "classify"
        super().__init__(state, llm_provider)
        self._split_threshold: int = 50

    def _should_split(self, file_count: int) -> bool:
        return file_count > self._split_threshold


class ScanAgent(DeepAnalyseAgent):
    """Phase 2: Static scanning via Docker sandbox tool server."""

    def __init__(self, state: AgentState, llm_provider: Any = None) -> None:
        state.role = "scan"
        super().__init__(state, llm_provider)
        self._split_threshold: int = 100

    def _should_split(self, file_count: int) -> bool:
        return file_count > self._split_threshold


class AnalyzeAgent(DeepAnalyseAgent):
    """Phase 3: Per-file deep vulnerability analysis."""

    def __init__(self, state: AgentState, llm_provider: Any = None) -> None:
        state.role = "analyze"
        super().__init__(state, llm_provider)
        self._split_threshold: int = 50

    def _should_split(self, file_count: int) -> bool:
        return file_count > self._split_threshold


class CrossLinkAgent(DeepAnalyseAgent):
    """Phase 4: Cross-file correlation analysis."""

    def __init__(self, state: AgentState, llm_provider: Any = None) -> None:
        state.role = "cross-link"
        super().__init__(state, llm_provider)
        self._split_threshold: int = 30

    def _should_split(self, finding_count: int) -> bool:
        return finding_count > self._split_threshold


class ReviewAgent(DeepAnalyseAgent):
    """Phase 5: Findings deduplication, cross-validation, and refinement."""

    def __init__(self, state: AgentState, llm_provider: Any = None) -> None:
        state.role = "review"
        super().__init__(state, llm_provider)
        self._split_threshold: int = 50

    def _should_split(self, finding_count: int) -> bool:
        return finding_count > self._split_threshold
```

- [ ] **Step 3: 运行测试**

```bash
.venv/bin/pytest tests/test_agent_subclasses.py -v
# Expected: 9 PASS
```

- [ ] **Step 4: Commit**

```bash
git add src/nano_strix/agents/per_file_lib/deep_agent.py tests/test_agent_subclasses.py
git commit -m "feat: add 6 agent subclasses (Root/Classify/Scan/Analyze/CrossLink/Review)"
```

---

### Task 11: deep_analysis.py 入口点

**Files:**
- Create: `src/nano_strix/agents/deep_analysis.py`
- Test: `tests/test_deep_analysis_entry.py`

- [ ] **Step 1: 编写入口点测试**

```python
# tests/test_deep_analysis_entry.py
import json
import pytest
from nano_strix.agents.deep_analysis import build_ipc_response, parse_ipc_input


def test_parse_ipc_input():
    msg = json.dumps({
        "type": "task", "task_id": "t-test",
        "stage": "deep_analysis",
        "payload": {"target": "/tmp/target", "stage_results": {}},
    })
    task_id, payload = parse_ipc_input(msg)
    assert task_id == "t-test"
    assert payload["target"] == "/tmp/target"


def test_build_ipc_response_success():
    resp = build_ipc_response("t-test", "ok", {
        "findings": [], "coverage_summary": {}, "timings": {},
    })
    data = json.loads(resp)
    assert data["type"] == "result"
    assert data["payload"]["status"] == "ok"


def test_build_ipc_response_error():
    resp = build_ipc_response("t-test", "error", {"error": "something broke"})
    data = json.loads(resp)
    assert data["payload"]["status"] == "error"
```

- [ ] **Step 2: 实现 deep_analysis.py**

```python
# src/nano_strix/agents/deep_analysis.py
#!/usr/bin/env python3
"""Deep Analysis Stage: root-agent-driven recursive file analysis.

Replaces per_file.py and cross_file.py with a unified stage that uses
a strix-style agent graph. The RootAgent orchestrates 5 phases:
classify → scan → analyze → cross-link → review.
"""

from __future__ import annotations

import asyncio
import json as _json
import logging
import sys
import time as _time
from pathlib import Path
from typing import Any

from nano_strix.agents.per_file_lib.deep_agent import RootAgent
from nano_strix.agents.per_file_lib.graph import (
    AgentState,
    create_agent,
    wait_for_message,
    send_message_to_agent,
    agent_finish,
    view_agent_graph,
    _agent_graph,
    _running_agents,
)
from nano_strix.agents.per_file_lib.manifest import FileManifest
from nano_strix.llm.factory import create_provider
from nano_strix.config.loader import load_config
from nano_strix.logging.setup import setup_logging

logger = logging.getLogger(__name__)

# Register graph tools for LLM access
from nano_strix.tools.registry import register_tool

# Re-register the graph primitives as tools
create_agent_tool = register_tool(create_agent)
send_message_tool = register_tool(send_message_to_agent)
wait_message_tool = register_tool(wait_for_message)
agent_finish_tool = register_tool(agent_finish)
view_graph_tool = register_tool(view_agent_graph)


def parse_ipc_input(raw: str) -> tuple[str, dict[str, Any]]:
    data = _json.loads(raw)
    return data["task_id"], data.get("payload", {})


def build_ipc_response(task_id: str, status: str, extra: dict[str, Any]) -> str:
    return _json.dumps({
        "type": "result",
        "task_id": task_id,
        "payload": {
            "status": status,
            "stage": "deep_analysis",
            **extra,
        },
    }, ensure_ascii=False)


def main() -> None:
    raw_input = sys.stdin.readline().strip()
    if not raw_input:
        response = build_ipc_response("unknown", "error", {"error": "No input received"})
        sys.stdout.write(response + "\n")
        sys.stdout.flush()
        return

    task_id, payload = parse_ipc_input(raw_input)
    target = payload.get("target", "")

    # Setup logging
    config = load_config()
    setup_logging(config.logging)

    logger.info("Deep analysis stage started: task=%s target=%s", task_id, target)

    t_start = _time.monotonic()
    timings: dict[str, float] = {}

    try:
        # Create LLM provider
        llm = create_provider(config.llm)

        # Initialize skills
        from nano_strix.skills.loader import SkillLoader, set_skill_loader
        skills_dir = Path(__file__).parent.parent / "skills"
        skill_loader = SkillLoader(skills_dir)
        skill_loader.load_all()
        set_skill_loader(skill_loader)

        # Create RootAgent state
        root_state = AgentState(
            agent_name="DeepAnalysisRoot",
            task=f"Orchestrate deep analysis of target: {target}",
            role="root",
            max_iterations=500,
            waiting_timeout=1800,  # 30 min per phase
        )

        root_agent = RootAgent(state=root_state, llm_provider=llm)

        # Run root agent (synchronous wrapper for the async agent_loop)
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            result = loop.run_until_complete(root_agent.agent_loop())
        finally:
            loop.close()

        # Wait for any remaining sub-agents
        import threading
        for agent_id, thread in list(_running_agents.items()):
            thread.join(timeout=30)

        # Collect results from agent graph
        all_findings = []
        for node in _agent_graph["nodes"].values():
            node_result = node.get("result", {}) or {}
            node_findings = node_result.get("findings", [])
            if isinstance(node_findings, list):
                all_findings.extend(node_findings)

        timings["total"] = _time.monotonic() - t_start

        logger.info("Deep analysis complete: %d findings in %.1fs", len(all_findings), timings["total"])

        response = build_ipc_response(task_id, "ok", {
            "target": target,
            "findings": all_findings,
            "coverage_summary": {},
            "timings": timings,
        })

    except Exception as e:
        logger.exception("Deep analysis failed")
        timings["total"] = _time.monotonic() - t_start
        response = build_ipc_response(task_id, "error", {
            "target": target,
            "error": str(e),
            "timings": timings,
        })

    sys.stdout.write(response + "\n")
    sys.stdout.flush()


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: 运行测试**

```bash
.venv/bin/pytest tests/test_deep_analysis_entry.py -v
# Expected: 3 PASS
```

- [ ] **Step 4: Commit**

```bash
git add src/nano_strix/agents/deep_analysis.py tests/test_deep_analysis_entry.py
git commit -m "feat: add deep_analysis.py stage entry point with RootAgent"
```

---

### Task 12: 配置 Schema 与 Orchestrator 更新

**Files:**
- Modify: `src/nano_strix/config/schema.py`
- Modify: `src/nano_strix/orchestrator/runner.py`
- Delete: `src/nano_strix/agents/cross_file.py`

- [ ] **Step 1: 更新配置 schema**

```python
# 在 src/nano_strix/config/schema.py 中追加 DeepAnalysisConfig

@dataclass
class DeepAnalysisConfig:
    """Configuration for the deep-analysis stage."""
    classification_model: str = "claude-haiku-4-5-20251001"
    analysis_model: str = "claude-sonnet-4-6"
    max_concurrent_llm: int = 4
    max_tokens: int = 4096
    temperature: float = 0.1
    phase1_split_threshold: int = 50
    phase2_split_threshold: int = 100
    phase3_split_threshold: int = 50
    phase4_split_threshold: int = 30
    phase5_split_threshold: int = 50
    per_file_timeout_seconds: int = 3600
    max_agent_iterations: int = 300
    agent_waiting_timeout: int = 600

@dataclass
class SkillsConfig:
    """Configuration for the skills loading system."""
    skills_dir: str = ""

@dataclass
class SandboxConfig:
    """Configuration for sandbox environments."""
    sandbox_type: str = "docker"  # docker | process
    image: str = "nano-strix-sandbox:latest"
    network: str = "none"
    tool_server_port: int = 8080
    resources: dict[str, Any] = field(default_factory=dict)


# 在 AppConfig 中追加字段:
@dataclass
class AppConfig:
    # ... existing fields ...
    deep_analysis: DeepAnalysisConfig = field(default_factory=DeepAnalysisConfig)
    skills: SkillsConfig = field(default_factory=SkillsConfig)
    # sandbox already exists
```

- [ ] **Step 2: 更新 STAGE_SCRIPTS 注册**

```python
# 修改 src/nano_strix/orchestrator/runner.py
STAGE_SCRIPTS = {
    "deep_analysis": str(_AGENTS_DIR / "deep_analysis.py"),
    "exploit":       str(_AGENTS_DIR / "exploit.py"),
    "report":        str(_AGENTS_DIR / "report.py"),
}
```

- [ ] **Step 3: 更新 CLI pipeline presets**

```python
# 修改 src/nano_strix/cli.py 中的 pipeline_presets
pipeline_presets = {
    "full":     ["deep_analysis", "exploit", "report"],
    "analysis": ["deep_analysis", "report"],
    "exploit_only": ["exploit", "report"],
    "quick":    ["deep_analysis", "report"],
}
```

- [ ] **Step 4: 更新 SchedulerConfig 默认值**

```python
# 修改 src/nano_strix/config/schema.py SchedulerConfig
@dataclass
class SchedulerConfig:
    stages: dict[str, StageConcurrency] = field(default_factory=lambda: {
        "deep_analysis": StageConcurrency(max_concurrent=2, max_retries=2),
        "exploit":       StageConcurrency(max_concurrent=1, max_retries=2),
        "report":        StageConcurrency(max_concurrent=1, max_retries=0),
    })
```

- [ ] **Step 5: 删除 cross_file.py**

```bash
rm src/nano_strix/agents/cross_file.py
```

- [ ] **Step 6: Commit**

```bash
git add src/nano_strix/config/schema.py src/nano_strix/orchestrator/runner.py src/nano_strix/cli.py
git rm src/nano_strix/agents/cross_file.py
git commit -m "feat: update config and orchestrator for deep-analysis stage"
```

---

### Task 13: 端到端集成测试

**Files:**
- Create: `tests/test_deep_analysis_integration.py`

- [ ] **Step 1: 编写集成测试**

```python
# tests/test_deep_analysis_integration.py
import json
import pytest
from nano_strix.agents.deep_analysis import main as deep_analysis_main
from nano_strix.agents.per_file_lib.graph import (
    _agent_graph, _agent_messages, _running_agents,
)


def _cleanup_globals():
    _agent_graph["nodes"].clear()
    _agent_graph["edges"].clear()
    _agent_messages.clear()
    _running_agents.clear()
    import nano_strix.agents.per_file_lib.graph as g
    g._root_agent_id = None
    g._agent_graph = {"nodes": {}, "edges": []}
    g._agent_messages = {}
    g._running_agents = {}
    g._agent_instances = {}
    g._agent_states = {}


@pytest.mark.integration
def test_deep_analysis_end_to_end(tmp_path, monkeypatch):
    """Full end-to-end test with a small target directory."""
    _cleanup_globals()

    # Create a small test target
    target_dir = tmp_path / "test_app"
    target_dir.mkdir()
    (target_dir / "app.py").write_text("""
from flask import Flask, request
import sqlite3

app = Flask(__name__)

@app.route('/login', methods=['POST'])
def login():
    user = request.form['username']
    pw = request.form['password']
    conn = sqlite3.connect('users.db')
    cursor = conn.cursor()
    cursor.execute(f"SELECT * FROM users WHERE name='{user}' AND password='{pw}'")
    return str(cursor.fetchone())
""")

    task_input = json.dumps({
        "type": "task",
        "task_id": "t-integration",
        "stage": "deep_analysis",
        "payload": {"target": str(target_dir), "stage_results": {}},
    })

    # Mock stdin/stdout
    import io
    fake_stdin = io.StringIO(task_input + "\n")
    fake_stdout = io.StringIO()

    monkeypatch.setattr("sys.stdin", fake_stdin)
    monkeypatch.setattr("sys.stdout", fake_stdout)

    # This test requires a real LLM provider and config to run fully.
    # For CI, mark as skipped.
    try:
        from nano_strix.config.loader import load_config
        load_config()
    except Exception:
        pytest.skip("No config available for integration test")

    deep_analysis_main()

    output = fake_stdout.getvalue().strip()
    assert output, "Should produce IPC output"
    result = json.loads(output)
    assert result["type"] == "result"
    assert result["payload"]["status"] in ("ok", "error")
```

- [ ] **Step 2: 运行集成测试**

```bash
.venv/bin/pytest tests/test_deep_analysis_integration.py -v -m "integration" --timeout 120
# Expected: SKIP (no config) or PASS (if config available)
```

- [ ] **Step 3: Commit**

```bash
git add tests/test_deep_analysis_integration.py
git commit -m "test: add end-to-end integration test for deep-analysis stage"
```

---

## 实现顺序总结

```
Task 1  (graph.py: AgentState + globals)
  └─ Task 2  (graph.py: 5 core primitives)
       └─ Task 3  (graph_schema.xml)
Task 4  (manifest merge)
Task 5  (OpenAICompatibleProvider) ── standalone
Task 6  (DockerSandbox + tool_server) ── standalone
Task 7  (Skills system) ── standalone
Task 8  (Prompt templates) ── standalone
       └─ Task 9  (DeepAnalyseAgent base) ── depends on Task 2 + Task 8
            └─ Task 10 (6 agent subclasses) ── depends on Task 9
                 └─ Task 11 (deep_analysis.py entry) ── depends on all above
                      └─ Task 12 (config + orchestrator)
                           └─ Task 13 (integration test)
```

**可并行化**：Task 4、Task 5、Task 6、Task 7、Task 8 之间无依赖，可同时执行。
