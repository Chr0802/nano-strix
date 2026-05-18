from __future__ import annotations

import json
import uuid
from pathlib import Path

from nano_strix.bus.events import TaskEvent, TaskState


class EventBus:
    def __init__(self, workspace_root: Path) -> None:
        self._root = workspace_root
        self._root.mkdir(parents=True, exist_ok=True)

    def create_task(self, stages: list[str]) -> TaskState:
        task_id = f"t-{uuid.uuid4().hex[:8]}"
        task_dir = self._root / task_id
        task_dir.mkdir(parents=True)

        state = TaskState(
            task_id=task_id, stages=stages,
            current_stage=None, status="pending",
        )
        self.update_state(state)
        return state

    def publish(self, event: TaskEvent) -> None:
        events_file = self._root / event.task_id / "events.jsonl"
        with open(events_file, "a") as f:
            f.write(json.dumps(event.to_dict(), ensure_ascii=False) + "\n")

    def get_events(self, task_id: str) -> list[TaskEvent]:
        events_file = self._root / task_id / "events.jsonl"
        if not events_file.exists():
            return []
        events = []
        for line in events_file.read_text().strip().split("\n"):
            if not line:
                continue
            data = json.loads(line)
            events.append(TaskEvent(
                task_id=data["task_id"],
                event_type=data["event_type"],
                stage=data.get("stage"),
                payload=data.get("payload", {}),
            ))
        return events

    def update_state(self, state: TaskState) -> None:
        state_file = self._root / state.task_id / "state.json"
        state_file.parent.mkdir(parents=True, exist_ok=True)
        with open(state_file, "w") as f:
            json.dump({
                "task_id": state.task_id,
                "stages": state.stages,
                "current_stage": state.current_stage,
                "status": state.status,
                "stage_results": state.stage_results,
                "error": state.error,
            }, f, ensure_ascii=False, indent=2)

    def get_state(self, task_id: str) -> TaskState:
        state_file = self._root / task_id / "state.json"
        with open(state_file) as f:
            data = json.load(f)
        return TaskState(
            task_id=data["task_id"],
            stages=data["stages"],
            current_stage=data["current_stage"],
            status=data["status"],
            stage_results=data.get("stage_results", {}),
            error=data.get("error"),
        )

    def get_pending_tasks(self) -> list[TaskState]:
        tasks = []
        for task_dir in self._root.iterdir():
            if task_dir.is_dir() and (task_dir / "state.json").exists():
                state = self.get_state(task_dir.name)
                if state.status == "pending":
                    tasks.append(state)
        return tasks
