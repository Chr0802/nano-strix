from __future__ import annotations

from pathlib import Path
from typing import Any

from nano_strix.logging.logger import JSONLLogger, LogEntry


class TaskLogger:
    def __init__(self, path: Path) -> None:
        self._logger = JSONLLogger(path)

    def task_created(self, task_id: str, stages: list[str]) -> None:
        self._logger.write(LogEntry(
            task_id=task_id, stage=None, category="task", level="info",
            event="task_created", data={"stages": stages},
        ))

    def task_started(
        self, task_id: str, stage: str,
        data: dict[str, Any] | None = None,
    ) -> None:
        self._logger.write(LogEntry(
            task_id=task_id, stage=stage, category="task",
            level="info", event="stage_started", data=data or {},
        ))

    def task_completed(
        self, task_id: str, stage: str,
        data: dict[str, Any] | None = None,
    ) -> None:
        self._logger.write(LogEntry(
            task_id=task_id, stage=stage, category="task",
            level="info", event="stage_completed", data=data or {},
        ))

    def task_failed(self, task_id: str, stage: str, error: str) -> None:
        self._logger.write(LogEntry(
            task_id=task_id, stage=stage, category="task", level="error",
            event="stage_failed", data={"error": error},
        ))
