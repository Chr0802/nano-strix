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

        finish_map = {
            "stop": "stop",
            "tool_calls": "tool_calls",
            "length": "stop",
            "content_filter": "stop",
        }
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
            openai_tools = [
                {
                    "type": "function",
                    "function": {
                        "name": t["name"],
                        "description": t.get("description", ""),
                        "parameters": t.get("parameters", t.get("input_schema", {})),
                    },
                }
                for t in tools
            ]
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
