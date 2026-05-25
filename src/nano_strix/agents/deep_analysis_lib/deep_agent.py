from __future__ import annotations

import logging
from typing import Any

from nano_strix.agents.deep_analysis_lib.graph import (
    AgentState,
    _agent_graph,
    _agent_messages,
    _agent_states,
    _agent_instances,
    _agent_graph_lock,
    _root_agent_id,
    _now_iso,
    get_graph_logger,
)
from nano_strix.agents.deep_analysis_lib.prompts import build_system_prompt

logger = logging.getLogger(__name__)


class DeepAnalyseAgent:
    """Base agent class for deep analysis. Runs on a daemon thread with its own asyncio event loop."""

    max_iterations: int = 300

    def __init__(
        self,
        state: AgentState,
        llm_provider: Any = None,
        llm_logger: Any = None,
        tool_logger: Any = None,
    ) -> None:
        self.state = state
        self._llm = llm_provider
        self._llm_logger = llm_logger
        self._tool_logger = tool_logger
        self._system_prompt = build_system_prompt(state.role) if state.role else ""
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
            import nano_strix.agents.deep_analysis_lib.graph as g
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
                    if get_graph_logger():
                        get_graph_logger().log_agent_status_change(
                            agent_id=self.state.agent_id,
                            old_status="waiting",
                            new_status="running",
                            reason="Waiting timeout",
                        )
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
            except Exception:
                logger.exception("Error in agent %s iteration %d", self.state.agent_name, self.state.iteration)
                if self.state.agent_id in _agent_graph["nodes"]:
                    _agent_graph["nodes"][self.state.agent_id]["status"] = "error"
                raise

    async def _process_iteration(self) -> bool:
        import time as _time
        from nano_strix.tools.executor import execute_tool_with_validation
        from nano_strix.tools.registry import get_tool_by_name

        messages = [{"role": "system", "content": self._system_prompt}] + self.state.get_conversation_history()
        tools = self._get_tools()

        # --- LLM Request Logging ---
        model_name = getattr(self._llm, 'model', 'unknown') if self._llm else 'unknown'
        if self._llm_logger:
            try:
                self._llm_logger.log_request_full(
                    task_id=self.state.task_id,
                    stage="deep_analysis",
                    model=model_name,
                    messages=messages,
                    tools=tools,
                )
            except Exception:
                logger.warning("LLM request logging failed", exc_info=True)

        t0 = _time.monotonic()
        response = await self._llm.chat(
            messages=messages,
            tools=tools,
            temperature=0.1,
            max_tokens=4096,
        )
        latency_ms = (_time.monotonic() - t0) * 1000

        # --- LLM Response Logging ---
        if self._llm_logger:
            try:
                self._llm_logger.log_response_full(
                    task_id=self.state.task_id,
                    stage="deep_analysis",
                    model=response.model or model_name,
                    content=response.content,
                    tool_calls=[
                        {"id": tc.id, "name": tc.name, "arguments": tc.arguments}
                        for tc in response.tool_calls
                    ],
                    input_tokens=response.usage.get("input_tokens", 0),
                    output_tokens=response.usage.get("output_tokens", 0),
                    latency_ms=latency_ms,
                    finish_reason=response.finish_reason,
                )
            except Exception:
                logger.warning("LLM response logging failed", exc_info=True)

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
                t_tool_start = _time.monotonic()
                try:
                    tool_fn = get_tool_by_name(tc.name)
                except KeyError:
                    result = {"error": f"Unknown tool: {tc.name}"}
                else:
                    try:
                        result = await execute_tool_with_validation(tc.name, tc.arguments)
                    except Exception as e:
                        result = {"error": str(e)}
                tool_elapsed = (_time.monotonic() - t_tool_start) * 1000

                # --- Tool Execution Logging ---
                if self._tool_logger:
                    try:
                        self._tool_logger.log_execution(
                            task_id=self.state.task_id,
                            stage="deep_analysis",
                            tool=tc.name,
                            arguments=tc.arguments,
                            result=result,
                            duration_ms=tool_elapsed,
                        )
                    except Exception:
                        logger.warning("Tool execution logging failed", exc_info=True)

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
                if get_graph_logger():
                    get_graph_logger().log_agent_status_change(
                        agent_id=agent_id,
                        old_status="waiting",
                        new_status="running",
                        reason="Message received",
                    )

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
        from nano_strix.tools.registry import tools as registered_tools, get_tool_param_schema
        result = []
        for td in registered_tools:
            schema = {}
            param_schema = get_tool_param_schema(td["name"])
            if param_schema:
                schema["input_schema"] = param_schema
            result.append({
                "name": td["name"],
                "description": td.get("description", ""),
                **schema,
            })
        return result


class RootAgent(DeepAnalyseAgent):
    """Root orchestrator: schedules phases, manages manifest coverage."""

    def __init__(self, state: AgentState, llm_provider: Any = None, llm_logger: Any = None, tool_logger: Any = None) -> None:
        state.role = "root"
        super().__init__(state, llm_provider, llm_logger=llm_logger, tool_logger=tool_logger)
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
