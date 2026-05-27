"""Stage-level progress tracking for the deep analysis harness."""

from __future__ import annotations

import threading
import time as _time
from dataclasses import dataclass, field
from enum import Enum


class StageStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    VALIDATING = "validating"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class StageProgress:
    stage_name: str
    status: StageStatus = StageStatus.PENDING
    agent_ids: list[str] = field(default_factory=list)
    retry_counts: dict[str, int] = field(default_factory=dict)
    started_at: float | None = None
    completed_at: float | None = None
    last_checkpoint: str = ""
    artifacts: list[str] = field(default_factory=list)

    def register_agent(self, agent_id: str) -> None:
        if agent_id not in self.agent_ids:
            self.agent_ids.append(agent_id)
        self.retry_counts.setdefault(agent_id, 0)

    def increment_retry(self, agent_id: str) -> int:
        self.retry_counts[agent_id] = self.retry_counts.get(agent_id, 0) + 1
        return self.retry_counts[agent_id]

    def all_agents_finished(self, finished_agent_ids: set[str]) -> bool:
        return set(self.agent_ids).issubset(finished_agent_ids)

    def is_terminal(self) -> bool:
        return self.status in (StageStatus.COMPLETED, StageStatus.FAILED)


class StageStateManager:
    """Thread-safe singleton manager for stage progress tracking."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._stages: dict[str, StageProgress] = {}

    def get_or_create(self, stage_name: str) -> StageProgress:
        with self._lock:
            if stage_name not in self._stages:
                self._stages[stage_name] = StageProgress(stage_name=stage_name)
            return self._stages[stage_name]

    def get(self, stage_name: str) -> StageProgress | None:
        with self._lock:
            return self._stages.get(stage_name)

    def transition(
        self,
        stage_name: str,
        to_status: StageStatus,
        checkpoint_detail: str = "",
    ) -> StageProgress:
        with self._lock:
            sp = self.get_or_create(stage_name)
            sp.status = to_status
            sp.last_checkpoint = checkpoint_detail
            if to_status == StageStatus.IN_PROGRESS and sp.started_at is None:
                sp.started_at = _time.monotonic()
            if to_status == StageStatus.COMPLETED:
                sp.completed_at = _time.monotonic()
            if to_status == StageStatus.FAILED:
                sp.completed_at = _time.monotonic()
            return sp

    def add_artifact(self, stage_name: str, artifact_path: str) -> None:
        with self._lock:
            sp = self.get_or_create(stage_name)
            if artifact_path not in sp.artifacts:
                sp.artifacts.append(artifact_path)

    def all_completed(self) -> bool:
        with self._lock:
            for sp in self._stages.values():
                if sp.status != StageStatus.COMPLETED:
                    return False
            return True

    def to_dict(self) -> dict[str, dict]:
        with self._lock:
            result: dict[str, dict] = {}
            for name, sp in self._stages.items():
                result[name] = {
                    "status": sp.status.value,
                    "agent_count": len(sp.agent_ids),
                    "retry_counts": dict(sp.retry_counts),
                    "started_at": sp.started_at,
                    "completed_at": sp.completed_at,
                    "last_checkpoint": sp.last_checkpoint,
                    "artifacts": list(sp.artifacts),
                }
            return result

    def reset(self) -> None:
        with self._lock:
            self._stages.clear()


# Module-level singleton
_stage_state_manager: StageStateManager | None = None
_lock = threading.Lock()


def get_stage_state_manager() -> StageStateManager:
    global _stage_state_manager
    with _lock:
        if _stage_state_manager is None:
            _stage_state_manager = StageStateManager()
        return _stage_state_manager


def reset_stage_state_manager() -> None:
    global _stage_state_manager
    with _lock:
        if _stage_state_manager is not None:
            _stage_state_manager.reset()
        _stage_state_manager = None
