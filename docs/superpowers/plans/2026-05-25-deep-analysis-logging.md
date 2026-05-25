# Deep Analysis Logging Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire structured JSONL loggers (LLM, tool, graph) into the deep analysis agent, writing to `{task_workspace}/logs/{llm,tools,graph}.jsonl`.

**Architecture:** Extend existing `LLMLogger`/`ToolLogger` with full-content methods, create new `GraphLogger`, inject them into `DeepAnalyseAgent` (constructor) and `graph.py` (module-level variable, consistent with existing `_agent_graph` pattern). Add `task_id` to `AgentState` so agent methods can reference it for logging.

**Tech Stack:** Python, pytest, existing `JSONLLogger`/`LogEntry` infrastructure, `FakeLLM` from tests for integration testing.

---

### Task 1: Add `task_id` to `AgentState`

**Files:**
- Modify: `src/nano_strix/agents/deep_analysis_lib/graph.py:13-31`
- Modify: `src/nano_strix/agents/deep_analysis.py:97-103`

- [ ] **Step 1: Add `task_id` field to `AgentState` dataclass**

In `src/nano_strix/agents/deep_analysis_lib/graph.py`, add the field:

```python
@dataclass
class AgentState:
    agent_id: str = field(default_factory=lambda: f"agent_{uuid.uuid4().hex[:8]}")
    agent_name: str = "DeepAnalyseAgent"
    parent_id: str | None = None
    task: str = ""
    task_id: str = ""  # <-- ADD
    role: str = ""
    # ... rest unchanged
```

- [ ] **Step 2: Pass `task_id` when creating `RootAgent` state in `deep_analysis.py`**

In `src/nano_strix/agents/deep_analysis.py`, update the `AgentState` constructor call:

```python
root_state = AgentState(
    agent_name="DeepAnalysisRoot",
    task_id=task_id,  # <-- ADD
    task=f"Orchestrate deep analysis of target: {target}",
    role="root",
    max_iterations=500,
    waiting_timeout=1800,
)
```

- [ ] **Step 3: Run existing tests to verify no regression**

```
.venv/bin/pytest tests/test_deep_agent.py tests/test_deep_analysis_entry.py tests/test_graph_state.py -v
```

Expected: all PASS.

- [ ] **Step 4: Commit**

```bash
git add src/nano_strix/agents/deep_analysis_lib/graph.py src/nano_strix/agents/deep_analysis.py
git commit -m "feat: add task_id field to AgentState for logging context"
```

---

### Task 2: Extend `LLMLogger` with full-content methods

**Files:**
- Modify: `src/nano_strix/logging/llm_logger.py`

- [ ] **Step 1: Add `log_request_full()` method**

In `src/nano_strix/logging/llm_logger.py`, add after `log_request()`:

```python
def log_request_full(
    self,
    task_id: str,
    stage: str,
    model: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
) -> None:
    self._logger.write(
        LogEntry(
            task_id=task_id,
            stage=stage,
            category="llm",
            level="debug",
            event="llm_request_full",
            data={
                "model": model,
                "messages": messages,
                "tools": tools or [],
                "messages_count": len(messages),
                "tools_count": len(tools) if tools else 0,
            },
        )
    )
```

- [ ] **Step 2: Add `log_response_full()` method**

In the same file, add after `log_response()`:

```python
def log_response_full(
    self,
    task_id: str,
    stage: str,
    model: str,
    content: str | None,
    tool_calls: list[dict[str, Any]] | None = None,
    input_tokens: int = 0,
    output_tokens: int = 0,
    latency_ms: float = 0.0,
    finish_reason: str = "stop",
) -> None:
    self._logger.write(
        LogEntry(
            task_id=task_id,
            stage=stage,
            category="llm",
            level="info",
            event="llm_response_full",
            data={
                "model": model,
                "content": content or "",
                "tool_calls": tool_calls or [],
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "latency_ms": latency_ms,
                "finish_reason": finish_reason,
            },
            duration=latency_ms / 1000,
        )
    )
```

- [ ] **Step 3: Run existing logging tests**

```
.venv/bin/pytest tests/test_logging.py -v
```

Expected: all PASS (existing `LLMLogger` tests unchanged).

- [ ] **Step 4: Commit**

```bash
git add src/nano_strix/logging/llm_logger.py
git commit -m "feat: add log_request_full and log_response_full to LLMLogger"
```

---

### Task 3: Extend `ToolLogger` with full result recording

**Files:**
- Modify: `src/nano_strix/logging/tool_logger.py`

- [ ] **Step 1: Update `log_execution()` to accept and record full result**

Replace the existing `log_execution` method:

```python
def log_execution(
    self,
    task_id: str,
    stage: str,
    tool: str,
    arguments: dict[str, Any],
    result: Any,
    duration_ms: float,
) -> None:
    result_str = str(result)
    self._logger.write(
        LogEntry(
            task_id=task_id,
            stage=stage,
            category="tool",
            level="info",
            event="tool_execution",
            data={
                "tool": tool,
                "arguments": arguments,
                "result": result,
                "result_chars": len(result_str),
            },
            duration=duration_ms / 1000,
        )
    )
```

- [ ] **Step 2: Update existing test to match new signature**

In `tests/test_logging.py`, update `test_tool_logger`:

```python
def test_tool_logger(tmp_path: Path):
    log_file = tmp_path / "tool.jsonl"
    logger = ToolLogger(log_file)
    logger.log_execution(
        "t-001",
        "per_file",
        "read_file",
        {"path": "src/a.py"},
        {"content": "file content here", "lines": 100},
        5.0,
    )

    lines = log_file.read_text().strip().split("\n")
    assert len(lines) == 1
    data = json.loads(lines[0])
    assert data["event"] == "tool_execution"
    assert data["data"]["tool"] == "read_file"
    assert data["data"]["result"] == {"content": "file content here", "lines": 100}
    assert data["data"]["result_chars"] > 0
```

- [ ] **Step 3: Run the test to verify**

```
.venv/bin/pytest tests/test_logging.py::test_tool_logger -v
```

Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add src/nano_strix/logging/tool_logger.py tests/test_logging.py
git commit -m "feat: extend ToolLogger to record full arguments and result"
```

---

### Task 4: Create `GraphLogger` class

**Files:**
- Create: `src/nano_strix/logging/graph_logger.py`
- Modify: `tests/test_logging.py` (add unit tests for GraphLogger)

- [ ] **Step 1: Write the failing tests for `GraphLogger`**

In `tests/test_logging.py`, add:

```python
def test_graph_logger_agent_created(tmp_path: Path):
    from nano_strix.logging.graph_logger import GraphLogger

    log_file = tmp_path / "graph.jsonl"
    logger = GraphLogger(log_file, task_id="t-001", stage="deep_analysis")
    logger.log_agent_created(
        agent_id="agent_abc123",
        parent_id="agent_root",
        name="Scanner-1",
        task="Scan files for vulnerabilities",
    )

    lines = log_file.read_text().strip().split("\n")
    assert len(lines) == 1
    data = json.loads(lines[0])
    assert data["task_id"] == "t-001"
    assert data["stage"] == "deep_analysis"
    assert data["category"] == "graph"
    assert data["event"] == "agent_created"
    assert data["data"]["agent_id"] == "agent_abc123"
    assert data["data"]["parent_id"] == "agent_root"
    assert data["data"]["name"] == "Scanner-1"


def test_graph_logger_status_change(tmp_path: Path):
    from nano_strix.logging.graph_logger import GraphLogger

    log_file = tmp_path / "graph.jsonl"
    logger = GraphLogger(log_file, task_id="t-001")
    logger.log_agent_status_change(
        agent_id="agent_abc123",
        old_status="running",
        new_status="waiting",
        reason="Waiting for cross-file analysis results",
    )

    lines = log_file.read_text().strip().split("\n")
    assert len(lines) == 1
    data = json.loads(lines[0])
    assert data["event"] == "agent_status_change"
    assert data["data"]["old_status"] == "running"
    assert data["data"]["new_status"] == "waiting"


def test_graph_logger_message_sent(tmp_path: Path):
    from nano_strix.logging.graph_logger import GraphLogger

    log_file = tmp_path / "graph.jsonl"
    logger = GraphLogger(log_file, task_id="t-001")
    logger.log_message_sent(
        from_id="agent_abc",
        to_id="agent_def",
        msg_id="msg_123",
        msg_type="information",
        priority="high",
    )

    lines = log_file.read_text().strip().split("\n")
    assert len(lines) == 1
    data = json.loads(lines[0])
    assert data["event"] == "message_sent"
    assert data["data"]["from"] == "agent_abc"
    assert data["data"]["to"] == "agent_def"


def test_graph_logger_agent_finished(tmp_path: Path):
    from nano_strix.logging.graph_logger import GraphLogger

    log_file = tmp_path / "graph.jsonl"
    logger = GraphLogger(log_file, task_id="t-001")
    logger.log_agent_finished(
        agent_id="agent_abc123",
        success=True,
        findings_count=3,
        result_summary="Found 3 vulnerabilities",
    )

    lines = log_file.read_text().strip().split("\n")
    assert len(lines) == 1
    data = json.loads(lines[0])
    assert data["event"] == "agent_finished"
    assert data["data"]["success"] is True
    assert data["data"]["findings_count"] == 3
```

- [ ] **Step 2: Run tests to verify they fail**

```
.venv/bin/pytest tests/test_logging.py::test_graph_logger_agent_created tests/test_logging.py::test_graph_logger_status_change tests/test_logging.py::test_graph_logger_message_sent tests/test_logging.py::test_graph_logger_agent_finished -v
```

Expected: all FAIL with `ModuleNotFoundError` (no `graph_logger` module yet) or `ImportError`.

- [ ] **Step 3: Harden `JSONLLogger.write()` and `LogEntry.to_json()` against serialization failures**

In `src/nano_strix/logging/logger.py`, update `LogEntry.to_json()` to use `default=repr`:

```python
def to_json(self) -> str:
    return json.dumps(
        {
            "timestamp": self.timestamp.isoformat(),
            "task_id": self.task_id,
            "stage": self.stage,
            "category": self.category,
            "level": self.level,
            "event": self.event,
            "data": self.data,
            "duration": self.duration,
        },
        ensure_ascii=False,
        default=repr,  # <-- ADD: handle non-serializable objects
    )
```

Update `JSONLLogger.write()` to catch all exceptions:

```python
def write(self, entry: LogEntry) -> None:
    try:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._path, "a") as f:
            f.write(entry.to_json() + "\n")
    except Exception:
        import logging
        logging.warning(
            "JSONLLogger: failed to write log entry task=%s event=%s",
            entry.task_id, entry.event,
        )
```

- [ ] **Step 4: Implement `GraphLogger`**

Create `src/nano_strix/logging/graph_logger.py`:

```python
from __future__ import annotations

from pathlib import Path
from typing import Any

from nano_strix.logging.logger import JSONLLogger, LogEntry


class GraphLogger:
    def __init__(
        self,
        path: Path,
        task_id: str = "",
        stage: str = "deep_analysis",
    ) -> None:
        self._logger = JSONLLogger(path)
        self._task_id = task_id
        self._stage = stage

    def _write(self, event: str, data: dict[str, Any]) -> None:
        try:
            self._logger.write(
                LogEntry(
                    task_id=self._task_id,
                    stage=self._stage,
                    category="graph",
                    level="info",
                    event=event,
                    data=data,
                )
            )
        except Exception:
            import logging
            logging.warning("GraphLogger: failed to write event %s", event)

    def log_agent_created(
        self,
        agent_id: str,
        parent_id: str | None,
        name: str,
        task: str,
    ) -> None:
        self._write(
            "agent_created",
            {
                "agent_id": agent_id,
                "parent_id": parent_id,
                "name": name,
                "task": task,
            },
        )

    def log_agent_status_change(
        self,
        agent_id: str,
        old_status: str,
        new_status: str,
        reason: str = "",
    ) -> None:
        self._write(
            "agent_status_change",
            {
                "agent_id": agent_id,
                "old_status": old_status,
                "new_status": new_status,
                "reason": reason,
            },
        )

    def log_message_sent(
        self,
        from_id: str,
        to_id: str,
        msg_id: str,
        msg_type: str,
        priority: str,
    ) -> None:
        self._write(
            "message_sent",
            {
                "from": from_id,
                "to": to_id,
                "msg_id": msg_id,
                "msg_type": msg_type,
                "priority": priority,
            },
        )

    def log_agent_finished(
        self,
        agent_id: str,
        success: bool,
        findings_count: int,
        result_summary: str,
    ) -> None:
        self._write(
            "agent_finished",
            {
                "agent_id": agent_id,
                "success": success,
                "findings_count": findings_count,
                "result_summary": result_summary,
            },
        )
```

- [ ] **Step 5: Run tests to verify they pass**

```
.venv/bin/pytest tests/test_logging.py::test_graph_logger_agent_created tests/test_logging.py::test_graph_logger_status_change tests/test_logging.py::test_graph_logger_message_sent tests/test_logging.py::test_graph_logger_agent_finished -v
```

Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add src/nano_strix/logging/graph_logger.py src/nano_strix/logging/logger.py tests/test_logging.py
git commit -m "feat: add GraphLogger for agent graph event recording"
```

---

### Task 5: Wire loggers into `DeepAnalyseAgent._process_iteration()`

**Files:**
- Modify: `src/nano_strix/agents/deep_analysis_lib/deep_agent.py`

- [ ] **Step 1: Update constructor to accept and store loggers**

In `src/nano_strix/agents/deep_analysis_lib/deep_agent.py`, update `DeepAnalyseAgent.__init__`:

```python
class DeepAnalyseAgent:
    """Base agent class for deep analysis. Runs on a daemon thread with its own asyncio event loop."""

    max_iterations: int = 300

    def __init__(
        self,
        state: AgentState,
        llm_provider: Any = None,
        llm_logger: Any = None,   # <-- ADD
        tool_logger: Any = None,  # <-- ADD
    ) -> None:
        self.state = state
        self._llm = llm_provider
        self._llm_logger = llm_logger     # <-- ADD
        self._tool_logger = tool_logger   # <-- ADD
        self._system_prompt = build_system_prompt(state.role) if state.role else ""
        self._register_in_graph()
```

- [ ] **Step 2: Update `_process_iteration()` with LLM and tool logging**

Replace the existing `_process_iteration` method:

```python
async def _process_iteration(self) -> bool:
    import time as _time
    from nano_strix.tools.executor import execute_tool_with_validation
    from nano_strix.tools.registry import get_tool_by_name

    messages = [{"role": "system", "content": self._system_prompt}] + self.state.get_conversation_history()
    tools = self._get_tools()

    # --- LLM Request Logging ---
    model_name = getattr(self._llm, 'model', 'unknown') if self._llm else 'unknown'
    if self._llm_logger:
        self._llm_logger.log_request_full(
            task_id=self.state.task_id,
            stage="deep_analysis",
            model=model_name,
            messages=messages,
            tools=tools,
        )

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
                self._tool_logger.log_execution(
                    task_id=self.state.task_id,
                    stage="deep_analysis",
                    tool=tc.name,
                    arguments=tc.arguments,
                    result=result,
                    duration_ms=tool_elapsed,
                )

            self.state.add_message("user", f"Tool result ({tc.name}): {str(result)[:2000]}")

            if tc.name == "agent_finish":
                should_finish = True

        return should_finish

    return False
```

- [ ] **Step 3: Run existing deep agent tests to verify no regression**

```
.venv/bin/pytest tests/test_deep_agent.py -v
```

Expected: all PASS (loggers are `None` by default, existing tests use default constructor).

- [ ] **Step 4: Commit**

```bash
git add src/nano_strix/agents/deep_analysis_lib/deep_agent.py
git commit -m "feat: wire LLM and tool loggers into DeepAnalyseAgent._process_iteration"
```

---

### Task 6: Wire `GraphLogger` into `graph.py` functions

**Files:**
- Modify: `src/nano_strix/agents/deep_analysis_lib/graph.py`

- [ ] **Step 1: Add module-level `_graph_logger` and accessor functions**

In `src/nano_strix/agents/deep_analysis_lib/graph.py`, add near the other module-level globals (after `_agent_graph_lock`):

```python
_graph_logger: Any = None


def set_graph_logger(logger: Any) -> None:
    """Set the GraphLogger instance for the current task."""
    global _graph_logger
    _graph_logger = logger
```

- [ ] **Step 2: Add `log_agent_created` call in `create_agent()`**

In `create_agent()`, right after the node is added to the graph (after line with `_agent_messages[child_state.agent_id] = []`), add:

```python
if _graph_logger:
    _graph_logger.log_agent_created(
        agent_id=child_state.agent_id,
        parent_id=parent_id,
        name=name,
        task=task,
    )
```

- [ ] **Step 3: Add `log_message_sent` call in `send_message_to_agent()`**

In `send_message_to_agent()`, after the edge is appended and the target is signaled (after line `target_state.signal_wake()`), add:

```python
if _graph_logger:
    _graph_logger.log_message_sent(
        from_id=sender_id,
        to_id=target_agent_id,
        msg_id=msg_data["id"],
        msg_type=message_type,
        priority=priority,
    )
```

- [ ] **Step 4: Add `log_agent_status_change` call in `wait_for_message()`**

In `wait_for_message()`, after setting the node status to "waiting":

```python
if _graph_logger:
    _graph_logger.log_agent_status_change(
        agent_id=agent_id,
        old_status="running",
        new_status="waiting",
        reason=reason,
    )
```

- [ ] **Step 5: Add `log_agent_finished` call in `agent_finish()`**

In `agent_finish()`, after setting `agent_node["status"] = "finished"`:

```python
if _graph_logger:
    _graph_logger.log_agent_finished(
        agent_id=agent_id,
        success=success,
        findings_count=len(findings),
        result_summary=result_summary,
    )
```

- [ ] **Step 6: Add status recovery log in `DeepAnalyseAgent.agent_loop()` (in `deep_agent.py`)**

In `_check_agent_messages` when recovering from waiting, also log the status change. Actually, let's add it to `resume_from_waiting()` in `AgentState` — but that's in `graph.py`, which is cleaner.

Actually, the recovery from "waiting" to "running" happens inside `_check_agent_messages()` in `deep_agent.py`. Let me add a call there. Find the line:

```python
if self.state.waiting_for_input:
    self.state.resume_from_waiting()
    has_new = True
    if agent_id in _agent_graph["nodes"]:
        _agent_graph["nodes"][agent_id]["status"] = "running"
```

Add after the status change:

```python
if _graph_logger:
    _graph_logger.log_agent_status_change(
        agent_id=agent_id,
        old_status="waiting",
        new_status="running",
        reason="Message received",
    )
```

Similarly in `agent_loop()` when timeout fires:

```python
if self.state.has_waiting_timeout():
    self.state.resume_from_waiting()
    self.state.add_message("user", "Waiting timeout reached. Resuming.")
    if self.state.agent_id in _agent_graph["nodes"]:
        _agent_graph["nodes"][self.state.agent_id]["status"] = "running"
```

Add after:

```python
if _graph_logger:
    _graph_logger.log_agent_status_change(
        agent_id=self.state.agent_id,
        old_status="waiting",
        new_status="running",
        reason="Waiting timeout",
    )
```

- [ ] **Step 7: Run graph-related tests**

```
.venv/bin/pytest tests/test_graph_state.py tests/test_graph_primitives.py -v
```

Expected: all PASS (loggers are not set in tests, so `_graph_logger` is `None`).

- [ ] **Step 8: Commit**

```bash
git add src/nano_strix/agents/deep_analysis_lib/graph.py src/nano_strix/agents/deep_analysis_lib/deep_agent.py
git commit -m "feat: wire GraphLogger into graph.py core functions"
```

---

### Task 7: Wire logger creation and injection in `deep_analysis.py` entry point

**Files:**
- Modify: `src/nano_strix/agents/deep_analysis.py`

- [ ] **Step 1: Add logger imports and creation in `main()`**

In `src/nano_strix/agents/deep_analysis.py`, add imports at the top:

```python
from nano_strix.logging.llm_logger import LLMLogger
from nano_strix.logging.tool_logger import ToolLogger
from nano_strix.logging.graph_logger import GraphLogger
from nano_strix.agents.deep_analysis_lib.graph import set_graph_logger
```

- [ ] **Step 2: Create logger instances and inject them**

In `main()`, after the `setup_logging(config.logging)` line and before creating `RootAgent`:

```python
# Create structured JSONL loggers for this task
workspace_path = Path(target).parent if target else Path.cwd()
logs_dir = Path(payload.get("workspace", ".")) / "logs"
llm_logger = LLMLogger(logs_dir / "llm.jsonl")
tool_logger = ToolLogger(logs_dir / "tools.jsonl")
graph_logger = GraphLogger(logs_dir / "graph.jsonl", task_id=task_id)
set_graph_logger(graph_logger)
```

- [ ] **Step 3: Pass loggers to `RootAgent` constructor**

Update the `RootAgent` constructor call:

```python
root_agent = RootAgent(
    state=root_state,
    llm_provider=llm,
    llm_logger=llm_logger,
    tool_logger=tool_logger,
)
```

- [ ] **Step 4: Verify the module still imports cleanly**

```
.venv/bin/python -c "from nano_strix.agents.deep_analysis import main; print('OK')"
```

Expected: OK (no import errors).

- [ ] **Step 5: Commit**

```bash
git add src/nano_strix/agents/deep_analysis.py
git commit -m "feat: create and inject loggers in deep_analysis entry point"
```

---

### Task 8: Write agent logging integration test

**Files:**
- Create: `tests/test_deep_analysis_logging.py`

- [ ] **Step 1: Write the integration test**

Create `tests/test_deep_analysis_logging.py`:

```python
import json
from pathlib import Path

import pytest
from nano_strix.agents.deep_analysis_lib.deep_agent import DeepAnalyseAgent
from nano_strix.agents.deep_analysis_lib.graph import AgentState
from nano_strix.llm.adapter import LLMResponse, ToolCall
from nano_strix.logging.llm_logger import LLMLogger
from nano_strix.logging.tool_logger import ToolLogger


class FakeLLMWithToolCalls:
    """Fake LLM that returns a tool call then finishes."""
    def __init__(self):
        self.call_count = 0

    async def chat(self, messages, tools=None, temperature=0.7, max_tokens=4096):
        if self.call_count == 0:
            self.call_count += 1
            return LLMResponse(
                content=None,
                tool_calls=[
                    ToolCall(id="tc_1", name="agent_finish", arguments={
                        "result_summary": "All clear",
                        "findings": [],
                        "success": True,
                    })
                ],
                finish_reason="tool_calls",
                usage={"input_tokens": 100, "output_tokens": 50},
                model="test-model",
            )
        return LLMResponse(
            content="done",
            finish_reason="stop",
            usage={"input_tokens": 10, "output_tokens": 5},
            model="test-model",
        )

    async def stream_chat(self, messages, tools=None, temperature=0.7, max_tokens=4096):
        yield "done"


def test_agent_logs_llm_and_tool_events(tmp_path: Path):
    """Agent with loggers writes LLM and tool events to JSONL files."""
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir(parents=True)

    llm_logger = LLMLogger(logs_dir / "llm.jsonl")
    tool_logger = ToolLogger(logs_dir / "tools.jsonl")

    state = AgentState(
        agent_name="TestAgent",
        task="test task",
        task_id="t-test",
        role="analyze",
        max_iterations=3,
    )

    fake_llm = FakeLLMWithToolCalls()
    agent = DeepAnalyseAgent(
        state=state,
        llm_provider=fake_llm,
        llm_logger=llm_logger,
        tool_logger=tool_logger,
    )

    import asyncio
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        result = loop.run_until_complete(agent.agent_loop())
    finally:
        loop.close()

    # Assert LLM log
    llm_lines = (logs_dir / "llm.jsonl").read_text().strip().split("\n")
    assert len(llm_lines) >= 2  # at least one request and one response

    req = json.loads(llm_lines[0])
    assert req["event"] == "llm_request_full"
    assert req["task_id"] == "t-test"
    assert req["data"]["model"] == "test-model"
    assert "messages" in req["data"]
    assert len(req["data"]["messages"]) >= 2  # system + user

    resp = json.loads(llm_lines[1])
    assert resp["event"] == "llm_response_full"
    assert resp["data"]["input_tokens"] == 100
    assert resp["data"]["finish_reason"] == "tool_calls"

    # Assert tool log
    tool_lines = (logs_dir / "tools.jsonl").read_text().strip().split("\n")
    assert len(tool_lines) >= 1

    tool_event = json.loads(tool_lines[0])
    assert tool_event["event"] == "tool_execution"
    assert tool_event["data"]["tool"] == "agent_finish"
    assert tool_event["task_id"] == "t-test"


def test_agent_does_not_crash_when_logger_fails(tmp_path: Path, monkeypatch):
    """Agent loop completes even when logger.write raises an exception."""
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir(parents=True)

    llm_logger = LLMLogger(logs_dir / "llm.jsonl")
    # Make the underlying JSONLLogger.write raise
    monkeypatch.setattr(llm_logger._logger, "write", lambda entry: (_ for _ in ()).throw(RuntimeError("disk full")))

    state = AgentState(
        agent_name="TestAgent",
        task="test task",
        task_id="t-test",
        role="analyze",
        max_iterations=3,
    )

    fake_llm = FakeLLMWithToolCalls()
    agent = DeepAnalyseAgent(
        state=state,
        llm_provider=fake_llm,
        llm_logger=llm_logger,
    )

    import asyncio
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        result = loop.run_until_complete(agent.agent_loop())
    finally:
        loop.close()

    # Agent should complete despite logger failure
    assert result is not None
```

- [ ] **Step 2: Run the integration tests**

```
.venv/bin/pytest tests/test_deep_analysis_logging.py -v
```

Expected: both PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/test_deep_analysis_logging.py
git commit -m "test: add integration tests for deep analysis logging"
```

---

### Task 9: Write graph logging test

**Files:**
- Create: `tests/test_graph_logging.py`

- [ ] **Step 1: Write the graph logging test**

Create `tests/test_graph_logging.py`:

```python
import json
from pathlib import Path

from nano_strix.agents.deep_analysis_lib.graph import (
    AgentState,
    create_agent,
    send_message_to_agent,
    wait_for_message,
    agent_finish,
    set_graph_logger,
    _agent_graph,
    _agent_graph_lock,
)
from nano_strix.logging.graph_logger import GraphLogger


def test_create_agent_logs_graph_event(tmp_path: Path):
    """create_agent writes an agent_created event to graph.jsonl."""
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir(parents=True)

    # Clean up global state
    _agent_graph["nodes"].clear()
    _agent_graph["edges"].clear()

    graph_logger = GraphLogger(logs_dir / "graph.jsonl", task_id="t-test")
    set_graph_logger(graph_logger)

    parent_state = AgentState(
        agent_name="Parent",
        task="Delegate work",
        task_id="t-test",
        role="root",
    )
    # Register parent in graph
    with _agent_graph_lock:
        _agent_graph["nodes"][parent_state.agent_id] = {
            "id": parent_state.agent_id,
            "name": "Parent",
            "task": "Delegate work",
            "status": "running",
            "parent_id": None,
            "role": "root",
            "created_at": "",
            "finished_at": None,
            "result": None,
        }

    result = create_agent(parent_state, task="Scan files", name="Scanner")
    assert result["success"] is True

    lines = (logs_dir / "graph.jsonl").read_text().strip().split("\n")
    assert len(lines) >= 1

    event = json.loads(lines[0])
    assert event["event"] == "agent_created"
    assert event["data"]["name"] == "Scanner"
    assert event["data"]["parent_id"] == parent_state.agent_id


def test_send_message_logs_graph_event(tmp_path: Path):
    """send_message_to_agent writes a message_sent event."""
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir(parents=True)

    _agent_graph["nodes"].clear()
    _agent_graph["edges"].clear()

    graph_logger = GraphLogger(logs_dir / "graph.jsonl", task_id="t-test")
    set_graph_logger(graph_logger)

    sender = AgentState(agent_name="Sender", task="x", task_id="t-test", role="analyze")
    target = AgentState(agent_name="Target", task="y", task_id="t-test", role="analyze")

    with _agent_graph_lock:
        _agent_graph["nodes"][sender.agent_id] = {
            "id": sender.agent_id, "name": "Sender", "task": "x",
            "status": "running", "parent_id": None, "role": "analyze",
            "created_at": "", "finished_at": None, "result": None,
        }
        _agent_graph["nodes"][target.agent_id] = {
            "id": target.agent_id, "name": "Target", "task": "y",
            "status": "running", "parent_id": None, "role": "analyze",
            "created_at": "", "finished_at": None, "result": None,
        }

    result = send_message_to_agent(sender, target.agent_id, "hello")
    assert result["success"] is True

    lines = (logs_dir / "graph.jsonl").read_text().strip().split("\n")
    events = [json.loads(l)["event"] for l in lines]
    assert "message_sent" in events


def test_agent_finish_logs_graph_event(tmp_path: Path):
    """agent_finish writes an agent_finished event."""
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir(parents=True)

    _agent_graph["nodes"].clear()
    _agent_graph["edges"].clear()

    graph_logger = GraphLogger(logs_dir / "graph.jsonl", task_id="t-test")
    set_graph_logger(graph_logger)

    parent = AgentState(agent_name="Parent", task="x", task_id="t-test", role="root")
    child = AgentState(
        agent_name="Child", task="Analyze",
        parent_id=parent.agent_id, task_id="t-test", role="analyze",
    )

    with _agent_graph_lock:
        _agent_graph["nodes"][parent.agent_id] = {
            "id": parent.agent_id, "name": "Parent", "task": "x",
            "status": "running", "parent_id": None, "role": "root",
            "created_at": "", "finished_at": None, "result": None,
        }
        _agent_graph["nodes"][child.agent_id] = {
            "id": child.agent_id, "name": "Child", "task": "Analyze",
            "status": "running", "parent_id": parent.agent_id, "role": "analyze",
            "created_at": "", "finished_at": None, "result": None,
        }

    result = agent_finish(
        child,
        result_summary="Found 2 issues",
        findings=["issue1", "issue2"],
        success=True,
    )
    assert result["agent_completed"] is True

    lines = (logs_dir / "graph.jsonl").read_text().strip().split("\n")
    events = [json.loads(l)["event"] for l in lines]
    assert "agent_finished" in events
```

- [ ] **Step 2: Run the graph logging tests**

```
.venv/bin/pytest tests/test_graph_logging.py -v
```

Expected: all PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/test_graph_logging.py
git commit -m "test: add graph event logging tests"
```

---

### Task 10: Run full test suite

- [ ] **Step 1: Run the complete test suite**

```
.venv/bin/pytest -v
```

Expected: all tests PASS, no regressions.

- [ ] **Step 2: If any failures, fix and re-run until green**
