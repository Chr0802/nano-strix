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
