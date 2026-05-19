from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass
class TaskEvent:
    task_id: str
    event_type: str
    # task_created / task_started / stage_started / stage_completed
    # / stage_failed / task_completed / task_failed
    stage: str | None
    payload: dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "event_type": self.event_type,
            "stage": self.stage,
            "payload": self.payload,
            "timestamp": self.timestamp.isoformat(),
        }


@dataclass
class TaskState:
    task_id: str
    stages: list[str]
    current_stage: str | None
    status: str  # pending / running / completed / failed
    stage_results: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    retry_counts: dict[str, int] = field(default_factory=dict)

    @property
    def is_complete(self) -> bool:
        return all(s in self.stage_results for s in self.stages)

    def advance(self, stage: str) -> None:
        self.current_stage = stage
        self.status = "running"

    def complete_stage(self, stage: str, result: Any) -> None:
        self.stage_results[stage] = result
        self.current_stage = None

    def fail(self, error: str) -> None:
        self.status = "failed"
        self.error = error
