from __future__ import annotations

from pathlib import Path
from typing import Any

from nano_strix.logging.logger import JSONLLogger, LogEntry


class ToolLogger:
    def __init__(self, path: Path) -> None:
        self._logger = JSONLLogger(path)

    def log_execution(
        self,
        task_id: str,
        stage: str,
        tool: str,
        arguments: dict[str, Any],
        result_chars: int,
        duration_ms: float,
    ) -> None:
        self._logger.write(LogEntry(
            task_id=task_id, stage=stage, category="tool",
            level="info", event="tool_execution", data={
                "tool": tool, "arguments": arguments,
                "result_chars": result_chars,
            },
            duration=duration_ms / 1000,
        ))
