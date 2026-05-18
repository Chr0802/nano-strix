import pytest

from nano_strix.llm.adapter import LLMProvider, LLMResponse, ToolCall


def test_tool_call_creation():
    tc = ToolCall(id="tc-1", name="read_file", arguments={"path": "src/auth.py"})
    assert tc.id == "tc-1"
    assert tc.name == "read_file"


def test_llm_response_properties():
    resp = LLMResponse(
        content=None,
        tool_calls=[ToolCall(id="tc-1", name="read_file", arguments={})],
        finish_reason="tool_calls",
        usage={"input_tokens": 100, "output_tokens": 50},
        model="claude-sonnet-4-6",
    )
    assert resp.has_tool_calls is True
    assert resp.should_execute_tools is True


def test_llm_response_no_tools():
    resp = LLMResponse(
        content="Here is my analysis...",
        tool_calls=[],
        finish_reason="stop",
        usage={"input_tokens": 100, "output_tokens": 200},
        model="claude-sonnet-4-6",
    )
    assert resp.has_tool_calls is False
    assert resp.should_execute_tools is False


def test_llm_provider_is_abstract():
    with pytest.raises(TypeError):
        LLMProvider()
