from nano_strix.bus.events import TaskEvent, TaskState


def test_task_event_creation():
    event = TaskEvent(
        task_id="t-001",
        event_type="task_created",
        stage=None,
        payload={"stages": ["per_file", "report"]},
    )
    assert event.task_id == "t-001"
    assert event.event_type == "task_created"


def test_task_event_to_dict():
    event = TaskEvent(
        task_id="t-001",
        event_type="stage_started",
        stage="per_file",
        payload={},
    )
    d = event.to_dict()
    assert d["task_id"] == "t-001"
    assert d["stage"] == "per_file"


def test_task_state_creation():
    state = TaskState(
        task_id="t-001",
        stages=["per_file", "cross_file", "report"],
        current_stage=None,
        status="pending",
    )
    assert state.status == "pending"
    assert state.stage_results == {}


def test_task_state_advance():
    state = TaskState(
        task_id="t-001",
        stages=["per_file", "cross_file", "report"],
        current_stage=None,
        status="pending",
    )
    state.advance("per_file")
    assert state.current_stage == "per_file"
    assert state.status == "running"


def test_task_state_complete_stage():
    state = TaskState(
        task_id="t-001",
        stages=["per_file", "cross_file", "report"],
        current_stage="per_file",
        status="running",
    )
    state.complete_stage("per_file", {"output": "results/per_file_findings.json"})
    expected = {"output": "results/per_file_findings.json"}
    assert state.stage_results["per_file"] == expected
    assert state.current_stage is None


def test_task_state_is_complete():
    state = TaskState(
        task_id="t-001",
        stages=["per_file"],
        current_stage=None,
        status="running",
        stage_results={"per_file": {}},
    )
    assert state.is_complete is True
