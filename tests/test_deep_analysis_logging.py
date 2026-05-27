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
        self.model = "test-model"

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
    assert "message" in req["data"]
    assert isinstance(req["data"]["message"], dict)  # single message dict (last message)

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


def test_agent_does_not_crash_when_tool_logger_fails(tmp_path: Path, monkeypatch):
    """Agent loop completes even when ToolLogger._logger.write raises an exception."""
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir(parents=True)

    llm_logger = LLMLogger(logs_dir / "llm.jsonl")
    tool_logger = ToolLogger(logs_dir / "tools.jsonl")
    monkeypatch.setattr(tool_logger._logger, "write", lambda entry: (_ for _ in ()).throw(RuntimeError("disk full")))

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

    assert result is not None
