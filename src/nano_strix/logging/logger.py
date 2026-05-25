from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_logger = logging.getLogger(__name__)


def _sanitize_for_json(obj: Any) -> Any:
    """Convert non-JSON-serializable objects to serializable equivalents.

    Handles: set→list, Path→str, datetime→isoformat, bytes→str.
    Falls back to repr() for unknown types and emits a warning.
    """
    if isinstance(obj, set):
        return list(obj)
    if isinstance(obj, frozenset):
        return list(obj)
    if isinstance(obj, Path):
        return str(obj)
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, bytes):
        try:
            return obj.decode("utf-8")
        except UnicodeDecodeError:
            return obj.hex()
    if isinstance(obj, complex):
        return repr(obj)
    _logger.warning(
        "JSONLLogger: non-serializable type %s, falling back to repr()",
        type(obj).__name__,
    )
    return repr(obj)


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
            default=_sanitize_for_json,
        )


class JSONLLogger:
    def __init__(self, path: Path) -> None:
        self._path = path

    def write(self, entry: LogEntry) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._path, "a") as f:
                f.write(entry.to_json() + "\n")
        except Exception:
            import logging
            logging.warning(
                "JSONLLogger: failed to write log entry task=%s event=%s",
                entry.task_id, entry.event,
            )
