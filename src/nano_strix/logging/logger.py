from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass
class LogEntry:
    task_id: str
    stage: str | None
    category: str  # task / llm / tool / sandbox / ipc
    level: str  # debug / info / warning / error
    event: str
    data: dict[str, Any]
    duration: float | None = None
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_json(self) -> str:
        return json.dumps(
            {
                "timestamp": self.timestamp.isoformat(),
                "task_id": self.task_id,
                "stage": self.stage,
                "category": self.category,
                "level": self.level,
                "event": self.event,
                "data": self.data,
                "duration": self.duration,
            },
            ensure_ascii=False,
        )


class JSONLLogger:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, entry: LogEntry) -> None:
        with open(self._path, "a") as f:
            f.write(entry.to_json() + "\n")
