from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from nano_strix.llm.adapter import LLMProvider, LLMResponse, ToolCall
from nano_strix.llm.registry import register_provider


def _merge_system_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge system messages into the first user message.

    Some Anthropic-compatible proxies (e.g. DeepSeek) don't support the
    ``system`` role in messages. Prepend system content to the first user
    message so the prompt survives.
    """
    system_parts = []
    others = []
    for msg in messages:
        if msg.get("role") == "system":
            system_parts.append(msg.get("content", ""))
        else:
            others.append(msg)

    if not system_parts:
        return messages

    merged = "\n\n".join(system_parts)
    for msg in others:
        if msg.get("role") == "user":
            msg["content"] = merged + "\n\n" + msg.get("content", "")
            return others

    others.insert(0, {"role": "user", "content": merged})
    return others


@register_provider("anthropic")
class AnthropicProvider(LLMProvider):
    def __init__(
        self,
        api_key: str = "",
        base_url: str = "",
        model: str = "claude-sonnet-4-6",
    ) -> None:
        import os

        import anthropic

        self.model = model
        # The SDK reads ANTHROPIC_BASE_URL from the environment when base_url
        # is None, so we must mirror that to keep our merge logic in sync.
        self._base_url = base_url or os.environ.get("ANTHROPIC_BASE_URL", "")
        self._client = anthropic.AsyncAnthropic(
            api_key=api_key or None,
            base_url=base_url or None,
        )

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        if self._base_url:
            messages = _merge_system_messages(messages)

        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if tools:
            kwargs["tools"] = tools

        response = await self._client.messages.create(**kwargs)

        content = None
        tool_calls: list[ToolCall] = []
        for block in response.content:
            if block.type == "text":
                content = block.text
            elif block.type == "tool_use":
                tool_calls.append(
                    ToolCall(id=block.id, name=block.name, arguments=block.input)
                )

        reason_map = {"end_turn": "stop", "tool_use": "tool_calls"}
        finish_reason = reason_map.get(response.stop_reason, response.stop_reason)

        return LLMResponse(
            content=content,
            tool_calls=tool_calls,
            finish_reason=finish_reason,
            usage={
                "input_tokens": response.usage.input_tokens,
                "output_tokens": response.usage.output_tokens,
            },
            model=self.model,
        )

    async def stream_chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> AsyncIterator[str]:
        """Stream text chunks only. Use chat() for tool-calling flows."""
        if self._base_url:
            messages = _merge_system_messages(messages)

        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if tools:
            kwargs["tools"] = tools

        async with self._client.messages.stream(**kwargs) as stream:
            async for text in stream.text_stream:
                yield text
