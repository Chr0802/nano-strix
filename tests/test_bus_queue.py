from pathlib import Path

from nano_strix.bus.events import TaskEvent
from nano_strix.bus.queue import EventBus


def test_event_bus_create_task(tmp_path: Path):
    bus = EventBus(tmp_path)
    state = bus.create_task(["per_file", "report"])
    assert state.status == "pending"
    assert state.stages == ["per_file", "report"]
    assert (tmp_path / f"{state.task_id}" / "state.json").exists()


def test_event_bus_publish_and_get_events(tmp_path: Path):
    bus = EventBus(tmp_path)
    state = bus.create_task(["per_file"])

    event = TaskEvent(
        task_id=state.task_id,
        event_type="task_started",
        stage="per_file",
    )
    bus.publish(event)

    events = bus.get_events(state.task_id)
    assert len(events) == 1
    assert events[0].event_type == "task_started"


def test_event_bus_update_state(tmp_path: Path):
    bus = EventBus(tmp_path)
    state = bus.create_task(["per_file"])
    state.advance("per_file")
    bus.update_state(state)

    loaded = bus.get_state(state.task_id)
    assert loaded.current_stage == "per_file"
    assert loaded.status == "running"


def test_event_bus_get_pending_tasks(tmp_path: Path):
    bus = EventBus(tmp_path)
    bus.create_task(["per_file"])
    bus.create_task(["exploit"])

    pending = bus.get_pending_tasks()
    assert len(pending) == 2
