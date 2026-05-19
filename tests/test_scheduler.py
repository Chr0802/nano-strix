from __future__ import annotations

from pathlib import Path

import pytest

from nano_strix.agents.manager import AgentManager
from nano_strix.bus.queue import EventBus
from nano_strix.config.schema import (
    AppConfig,
    IPCConfig,
    PipelineConfig,
    SchedulerConfig,
    StageConcurrency,
)
from nano_strix.orchestrator.scheduler import StageScheduler


def _write_agent_script(path: Path, name: str, *, fail: bool = False) -> str:
    script = path / f"{name}_agent.py"
    if fail:
        script.write_text(
            "import sys, json\n"
            "line = sys.stdin.readline()\n"
            "msg = json.loads(line)\n"
            "print(json.dumps({'type': 'result', 'task_id': msg['task_id'], "
            "'stage': msg['stage'], 'payload': {'error': 'agent failure'}}))\n"
            "sys.exit(1)\n"
        )
    else:
        script.write_text(
            "import sys, json\n"
            "line = sys.stdin.readline()\n"
            "msg = json.loads(line)\n"
            "print(json.dumps({'type': 'result', 'task_id': msg['task_id'], "
            "'stage': msg['stage'], 'payload': {'findings': []}}))\n"
        )
    return str(script)


def _make_config(
    stages: list[str] | None = None,
    concurrency: dict[str, int] | None = None,
    retries: dict[str, int] | None = None,
) -> AppConfig:
    if stages is None:
        stages = ["per_file", "report"]
    stage_map = {}
    for s in stages:
        mc = (concurrency or {}).get(s, 1)
        mr = (retries or {}).get(s, 2)
        stage_map[s] = StageConcurrency(max_concurrent=mc, max_retries=mr)
    return AppConfig(
        pipeline=PipelineConfig(stages=stages),
        scheduler=SchedulerConfig(stages=stage_map),
        ipc=IPCConfig(timeout_seconds=10),
    )


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    return tmp_path


@pytest.fixture
def event_bus(workspace: Path) -> EventBus:
    return EventBus(workspace / "tasks")


@pytest.mark.asyncio
async def test_scheduler_submit_task(
    workspace: Path, event_bus: EventBus, tmp_path: Path
):
    config = _make_config()
    _write_agent_script(tmp_path, "per_file")
    _write_agent_script(tmp_path, "report")

    from nano_strix.orchestrator.runner import STAGE_SCRIPTS

    STAGE_SCRIPTS["per_file"] = str(tmp_path / "per_file_agent.py")
    STAGE_SCRIPTS["report"] = str(tmp_path / "report_agent.py")

    manager = AgentManager(workspace=workspace, config=config.ipc)
    scheduler = StageScheduler(
        workspace=workspace,
        config=config,
        agent_manager=manager,
        event_bus=event_bus,
    )

    task_id = await scheduler.submit_task("/some/target")
    state = event_bus.get_state(task_id)
    assert state.status == "pending"
    assert state.stages == ["per_file", "report"]


@pytest.mark.asyncio
async def test_scheduler_submit_batch(
    workspace: Path, event_bus: EventBus, tmp_path: Path
):
    config = _make_config()
    _write_agent_script(tmp_path, "per_file")
    _write_agent_script(tmp_path, "report")

    from nano_strix.orchestrator.runner import STAGE_SCRIPTS

    STAGE_SCRIPTS["per_file"] = str(tmp_path / "per_file_agent.py")
    STAGE_SCRIPTS["report"] = str(tmp_path / "report_agent.py")

    manager = AgentManager(workspace=workspace, config=config.ipc)
    scheduler = StageScheduler(
        workspace=workspace,
        config=config,
        agent_manager=manager,
        event_bus=event_bus,
    )

    task_ids = await scheduler.submit_batch(["/t1", "/t2", "/t3"])
    assert len(task_ids) == 3
    for tid in task_ids:
        state = event_bus.get_state(tid)
        assert state.status == "pending"


@pytest.mark.asyncio
async def test_scheduler_stage_advancement(
    workspace: Path, event_bus: EventBus, tmp_path: Path
):
    config = _make_config()
    _write_agent_script(tmp_path, "per_file")
    _write_agent_script(tmp_path, "report")

    from nano_strix.orchestrator.runner import STAGE_SCRIPTS

    STAGE_SCRIPTS["per_file"] = str(tmp_path / "per_file_agent.py")
    STAGE_SCRIPTS["report"] = str(tmp_path / "report_agent.py")

    manager = AgentManager(workspace=workspace, config=config.ipc)
    scheduler = StageScheduler(
        workspace=workspace,
        config=config,
        agent_manager=manager,
        event_bus=event_bus,
    )

    task_id = await scheduler.submit_task("/target")
    await scheduler.run()

    state = event_bus.get_state(task_id)
    assert state.status == "completed"
    assert "per_file" in state.stage_results
    assert "report" in state.stage_results


@pytest.mark.asyncio
async def test_scheduler_retry_on_failure(
    workspace: Path, event_bus: EventBus, tmp_path: Path
):
    fail_script = tmp_path / "per_file_agent.py"
    fail_script.write_text(
        "import sys, json\n"
        "line = sys.stdin.readline()\n"
        "msg = json.loads(line)\n"
        "print(json.dumps({'type': 'result', 'task_id': msg['task_id'], "
        "'stage': msg['stage'], 'payload': {'error': 'fail'}}))\n"
        "sys.exit(1)\n"
    )
    _write_agent_script(tmp_path, "report")

    from nano_strix.orchestrator.runner import STAGE_SCRIPTS

    STAGE_SCRIPTS["per_file"] = str(fail_script)
    STAGE_SCRIPTS["report"] = str(tmp_path / "report_agent.py")

    config = _make_config(retries={"per_file": 3, "report": 0})

    manager = AgentManager(workspace=workspace, config=config.ipc)
    scheduler = StageScheduler(
        workspace=workspace,
        config=config,
        agent_manager=manager,
        event_bus=event_bus,
    )

    task_id = await scheduler.submit_task("/target")
    await scheduler.run()

    state = event_bus.get_state(task_id)
    assert state.status == "failed"
    assert state.retry_counts.get("per_file") == 4


@pytest.mark.asyncio
async def test_scheduler_fail_after_max_retries(
    workspace: Path, event_bus: EventBus, tmp_path: Path
):
    fail_script = tmp_path / "per_file_agent.py"
    fail_script.write_text(
        "import sys, json\n"
        "line = sys.stdin.readline()\n"
        "msg = json.loads(line)\n"
        "print(json.dumps({'type': 'result', 'task_id': msg['task_id'], "
        "'stage': msg['stage'], 'payload': {'error': 'fail'}}))\n"
        "sys.exit(1)\n"
    )

    from nano_strix.orchestrator.runner import STAGE_SCRIPTS

    STAGE_SCRIPTS["per_file"] = str(fail_script)
    STAGE_SCRIPTS["report"] = str(tmp_path / "report_agent.py")

    config = _make_config(retries={"per_file": 1, "report": 0})

    manager = AgentManager(workspace=workspace, config=config.ipc)
    scheduler = StageScheduler(
        workspace=workspace,
        config=config,
        agent_manager=manager,
        event_bus=event_bus,
    )

    task_id = await scheduler.submit_task("/target")
    await scheduler.run()

    state = event_bus.get_state(task_id)
    assert state.status == "failed"
    assert "per_file" in (state.error or "")
    assert state.retry_counts.get("per_file") == 2


@pytest.mark.asyncio
async def test_scheduler_concurrency_limit(
    workspace: Path, event_bus: EventBus, tmp_path: Path
):
    slow_script = tmp_path / "per_file_agent.py"
    slow_script.write_text(
        "import sys, json, time\n"
        "line = sys.stdin.readline()\n"
        "msg = json.loads(line)\n"
        "time.sleep(0.1)\n"
        "print(json.dumps({'type': 'result', 'task_id': msg['task_id'], "
        "'stage': msg['stage'], 'payload': {'findings': []}}))\n"
    )
    _write_agent_script(tmp_path, "report")

    from nano_strix.orchestrator.runner import STAGE_SCRIPTS

    STAGE_SCRIPTS["per_file"] = str(slow_script)
    STAGE_SCRIPTS["report"] = str(tmp_path / "report_agent.py")

    config = _make_config(
        concurrency={"per_file": 1, "report": 1},
        retries={"per_file": 0, "report": 0},
    )

    manager = AgentManager(workspace=workspace, config=config.ipc)
    scheduler = StageScheduler(
        workspace=workspace,
        config=config,
        agent_manager=manager,
        event_bus=event_bus,
    )

    task_ids = await scheduler.submit_batch(["/t1", "/t2", "/t3"])
    await scheduler.run()

    for tid in task_ids:
        state = event_bus.get_state(tid)
        assert state.status == "completed"


@pytest.mark.asyncio
async def test_scheduler_resume_task_from_second_stage(
    workspace: Path, event_bus: EventBus, tmp_path: Path
):
    _write_agent_script(tmp_path, "per_file")
    _write_agent_script(tmp_path, "report")

    from nano_strix.orchestrator.runner import STAGE_SCRIPTS

    STAGE_SCRIPTS["per_file"] = str(tmp_path / "per_file_agent.py")
    STAGE_SCRIPTS["report"] = str(tmp_path / "report_agent.py")

    config = _make_config()
    manager = AgentManager(workspace=workspace, config=config.ipc)
    scheduler = StageScheduler(
        workspace=workspace,
        config=config,
        agent_manager=manager,
        event_bus=event_bus,
    )

    # Create a task and pre-populate per_file as completed
    state = event_bus.create_task(["per_file", "report"])
    task_id = state.task_id
    state.complete_stage("per_file", {"findings": [], "status": "ok"})
    event_bus.update_state(state)

    # Resume should put task into report stage queue and run it
    await scheduler.resume_task(task_id, "/target")
    await scheduler.run()

    final_state = event_bus.get_state(task_id)
    assert final_state.status == "completed"
    assert "per_file" in final_state.stage_results
    assert "report" in final_state.stage_results


@pytest.mark.asyncio
async def test_scheduler_resume_task_already_completed(
    workspace: Path, event_bus: EventBus, tmp_path: Path
):
    config = _make_config()
    manager = AgentManager(workspace=workspace, config=config.ipc)
    scheduler = StageScheduler(
        workspace=workspace,
        config=config,
        agent_manager=manager,
        event_bus=event_bus,
    )

    # Pre-populate a fully completed task
    state = event_bus.create_task(["per_file", "report"])
    task_id = state.task_id
    state.complete_stage("per_file", {"findings": []})
    state.complete_stage("report", {"report": ""})
    state.status = "completed"
    event_bus.update_state(state)

    # resume_task should be a no-op (no remaining stages to enqueue)
    await scheduler.resume_task(task_id, "/target")
    # _remaining should still be 0 (wasn't incremented)
    assert scheduler._remaining == 0


@pytest.mark.asyncio
async def test_scheduler_resume_failed_task(
    workspace: Path, event_bus: EventBus, tmp_path: Path
):
    _write_agent_script(tmp_path, "per_file")
    _write_agent_script(tmp_path, "report")

    from nano_strix.orchestrator.runner import STAGE_SCRIPTS

    STAGE_SCRIPTS["per_file"] = str(tmp_path / "per_file_agent.py")
    STAGE_SCRIPTS["report"] = str(tmp_path / "report_agent.py")

    config = _make_config()
    manager = AgentManager(workspace=workspace, config=config.ipc)
    scheduler = StageScheduler(
        workspace=workspace,
        config=config,
        agent_manager=manager,
        event_bus=event_bus,
    )

    # Pre-populate a failed task (per_file done, exploit failed)
    state = event_bus.create_task(["per_file", "report"])
    task_id = state.task_id
    state.complete_stage("per_file", {"findings": [], "status": "ok"})
    state.fail("exploit timed out")
    event_bus.update_state(state)

    # Resume should reset status to pending and run remaining stages
    await scheduler.resume_task(task_id, "/target")
    await scheduler.run()

    final_state = event_bus.get_state(task_id)
    assert final_state.status == "completed"
    assert "per_file" in final_state.stage_results
    assert "report" in final_state.stage_results
