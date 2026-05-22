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
    raw_tool_call = type(
        "obj",
        (object,),
        {
            "id": "call_123",
            "type": "function",
            "function": type(
                "obj",
                (object,),
                {
                    "name": "file_read",
                    "arguments": '{"path": "/tmp/test.py"}',
                },
            )(),
        },
    )()
    tc = provider._map_tool_call(raw_tool_call)
    assert tc.id == "call_123"
    assert tc.name == "file_read"
    assert tc.arguments == {"path": "/tmp/test.py"}
