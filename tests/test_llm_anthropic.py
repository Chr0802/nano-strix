from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nano_strix.llm.adapter import LLMResponse
from nano_strix.llm.anthropic import AnthropicProvider


@pytest.fixture
def provider():
    return AnthropicProvider(api_key="test-key", model="claude-sonnet-4-6")


def test_anthropic_provider_creation(provider):
    assert provider.model == "claude-sonnet-4-6"


def test_anthropic_provider_default_model():
    p = AnthropicProvider(api_key="test-key")
    assert p.model == "claude-sonnet-4-6"


def test_anthropic_provider_custom_model():
    p = AnthropicProvider(api_key="test-key", model="claude-opus-4-6")
    assert p.model == "claude-opus-4-6"


def test_anthropic_provider_registered():
    from nano_strix.llm.registry import get_provider_class

    cls = get_provider_class("anthropic")
    assert cls is AnthropicProvider


@pytest.mark.asyncio
async def test_anthropic_chat(provider):
    mock_text_block = MagicMock()
    mock_text_block.type = "text"
    mock_text_block.text = "Hello"

    mock_response = MagicMock()
    mock_response.content = [mock_text_block]
    mock_response.stop_reason = "end_turn"
    mock_response.usage = MagicMock(input_tokens=10, output_tokens=5)

    with patch.object(
        provider._client.messages,
        "create",
        new_callable=AsyncMock,
        return_value=mock_response,
    ):
        resp = await provider.chat([{"role": "user", "content": "Hi"}])
        assert isinstance(resp, LLMResponse)
        assert resp.content == "Hello"
        assert resp.finish_reason == "stop"
        assert resp.usage == {"input_tokens": 10, "output_tokens": 5}
        assert resp.model == "claude-sonnet-4-6"
        assert resp.tool_calls == []
        assert not resp.has_tool_calls


@pytest.mark.asyncio
async def test_anthropic_chat_with_tool_calls(provider):
    mock_text_block = MagicMock()
    mock_text_block.type = "text"
    mock_text_block.text = "Let me look that up."

    mock_tool_block = MagicMock()
    mock_tool_block.type = "tool_use"
    mock_tool_block.id = "toolu_123"
    mock_tool_block.name = "search"
    mock_tool_block.input = {"query": "test"}

    mock_response = MagicMock()
    mock_response.content = [mock_text_block, mock_tool_block]
    mock_response.stop_reason = "tool_use"
    mock_response.usage = MagicMock(input_tokens=20, output_tokens=15)

    with patch.object(
        provider._client.messages,
        "create",
        new_callable=AsyncMock,
        return_value=mock_response,
    ):
        resp = await provider.chat(
            [{"role": "user", "content": "Search for something"}],
            tools=[{"name": "search", "description": "Search"}],
        )
        assert resp.content == "Let me look that up."
        assert resp.finish_reason == "tool_calls"
        assert len(resp.tool_calls) == 1
        assert resp.tool_calls[0].id == "toolu_123"
        assert resp.tool_calls[0].name == "search"
        assert resp.tool_calls[0].arguments == {"query": "test"}
        assert resp.has_tool_calls


@pytest.mark.asyncio
async def test_anthropic_chat_text_only_no_content(provider):
    mock_response = MagicMock()
    mock_response.content = []
    mock_response.stop_reason = "end_turn"
    mock_response.usage = MagicMock(input_tokens=5, output_tokens=0)

    with patch.object(
        provider._client.messages,
        "create",
        new_callable=AsyncMock,
        return_value=mock_response,
    ):
        resp = await provider.chat([{"role": "user", "content": "Hi"}])
        assert resp.content is None


@pytest.mark.asyncio
async def test_anthropic_stream_chat(provider):
    async def _async_iter(items):
        for item in items:
            yield item

    mock_stream = MagicMock()
    mock_stream.text_stream = _async_iter(["Hello", " world", "!"])
    mock_stream.__aenter__ = AsyncMock(return_value=mock_stream)
    mock_stream.__aexit__ = AsyncMock(return_value=False)

    with patch.object(
        provider._client.messages,
        "stream",
        return_value=mock_stream,
    ):
        chunks = []
        async for chunk in provider.stream_chat([{"role": "user", "content": "Hi"}]):
            chunks.append(chunk)
        assert chunks == ["Hello", " world", "!"]
