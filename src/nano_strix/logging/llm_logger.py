from __future__ import annotations

from pathlib import Path
from typing import Any

from nano_strix.logging.logger import JSONLLogger, LogEntry


class LLMLogger:
    def __init__(self, path: Path) -> None:
        self._logger = JSONLLogger(path)

    def log_request(
        self,
        task_id: str,
        stage: str,
        model: str,
        messages_count: int,
        tools_count: int,
    ) -> None:
        self._logger.write(
            LogEntry(
                task_id=task_id,
                stage=stage,
                category="llm",
                level="debug",
                event="llm_request",
                data={
                    "model": model,
                    "messages_count": messages_count,
                    "tools_count": tools_count,
                },
            )
        )

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

    def log_response(
        self,
        task_id: str,
        stage: str,
        input_tokens: int,
        output_tokens: int,
        latency_ms: float,
        finish_reason: str,
    ) -> None:
        self._logger.write(
            LogEntry(
                task_id=task_id,
                stage=stage,
                category="llm",
                level="info",
                event="llm_response",
                data={
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "latency_ms": latency_ms,
                    "finish_reason": finish_reason,
                },
                duration=latency_ms / 1000,
            )
        )

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
