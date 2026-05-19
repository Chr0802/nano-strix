# CLI 补全 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 补全 CLI 的 `run`、`run-batch`、`resume` 命令，使其真正驱动 pipeline 执行，同时将四个 agent 脚本改为 sleep+log 模拟模式以方便调试。

**Architecture:** `_execute_pipeline()` 是核心辅助函数，封装 EventBus/AgentManager/StageScheduler 的创建与执行。`run` 和 `run-batch` 共用此函数。`resume` 通过 Scheduler 新增的 `resume_task()` 方法从断点恢复。四个 agent 脚本统一为模拟模式，通过 IPC 返回结果。

**Tech Stack:** Python 3.12+, Click, asyncio

---

### Task 1: Agent 脚本改造为模拟模式

**Files:**
- Modify: `src/nano_strix/agents/per_file.py`
- Modify: `src/nano_strix/agents/cross_file.py`
- Modify: `src/nano_strix/agents/exploit.py`
- Create: `src/nano_strix/agents/report.py`

- [ ] **Step 1: Rewrite per_file.py**

```python
"""strix-per-file agent: 模拟执行，便于 CLI 调试。"""
import json
import sys
import time
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s [per_file] %(message)s")
logger = logging.getLogger(__name__)

SLEEP_SECONDS = 2


def main():
    line = sys.stdin.readline()
    msg = json.loads(line)
    task_id = msg["task_id"]
    target = msg.get("payload", {}).get("target", "unknown")

    logger.info("Task %s: start processing %s", task_id, target)
    time.sleep(SLEEP_SECONDS)
    logger.info("Task %s: done", task_id)

    result = {
        "type": "result",
        "task_id": task_id,
        "payload": {
            "status": "ok",
            "findings": [],
            "stage": "per_file",
            "target": target,
        },
    }
    print(json.dumps(result))


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Rewrite cross_file.py**

```python
"""strix-cross-file agent: 模拟执行，便于 CLI 调试。"""
import json
import sys
import time
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s [cross_file] %(message)s")
logger = logging.getLogger(__name__)

SLEEP_SECONDS = 4


def main():
    line = sys.stdin.readline()
    msg = json.loads(line)
    task_id = msg["task_id"]
    target = msg.get("payload", {}).get("target", "unknown")

    logger.info("Task %s: start cross-file analysis on %s", task_id, target)
    time.sleep(SLEEP_SECONDS)
    logger.info("Task %s: done", task_id)

    result = {
        "type": "result",
        "task_id": task_id,
        "payload": {
            "status": "ok",
            "findings": [],
            "stage": "cross_file",
            "target": target,
        },
    }
    print(json.dumps(result))


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Rewrite exploit.py**

```python
"""strix-exploit agent: 模拟执行，便于 CLI 调试。"""
import json
import sys
import time
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s [exploit] %(message)s")
logger = logging.getLogger(__name__)

SLEEP_SECONDS = 5


def main():
    line = sys.stdin.readline()
    msg = json.loads(line)
    task_id = msg["task_id"]
    target = msg.get("payload", {}).get("target", "unknown")

    logger.info("Task %s: start exploit verification on %s", task_id, target)
    time.sleep(SLEEP_SECONDS)
    logger.info("Task %s: done", task_id)

    result = {
        "type": "result",
        "task_id": task_id,
        "payload": {
            "status": "ok",
            "exploit_results": [],
            "stage": "exploit",
            "target": target,
        },
    }
    print(json.dumps(result))


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Create report.py**

```python
"""strix-report agent: 模拟执行，便于 CLI 调试。"""
import json
import sys
import time
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s [report] %(message)s")
logger = logging.getLogger(__name__)

SLEEP_SECONDS = 1


def main():
    line = sys.stdin.readline()
    msg = json.loads(line)
    task_id = msg["task_id"]
    target = msg.get("payload", {}).get("target", "unknown")

    logger.info("Task %s: generating report for %s", task_id, target)
    time.sleep(SLEEP_SECONDS)
    logger.info("Task %s: report done", task_id)

    result = {
        "type": "result",
        "task_id": task_id,
        "payload": {
            "status": "ok",
            "report": "",
            "stage": "report",
            "target": target,
        },
    }
    print(json.dumps(result))


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: Run existing scheduler tests to verify agent change doesn't break IPC**

Run: `.venv/bin/pytest tests/test_scheduler.py -v`
Expected: All tests PASS (existing test agents are written inline by tests, not affected by this change)

- [ ] **Step 6: Commit**

```bash
git add src/nano_strix/agents/per_file.py src/nano_strix/agents/cross_file.py src/nano_strix/agents/exploit.py src/nano_strix/agents/report.py
git commit -m "feat: replace agent stubs with sleep+log simulation for CLI debugging

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 2: STAGE_SCRIPTS 增加 report 映射

**Files:**
- Modify: `src/nano_strix/orchestrator/runner.py:9-13`

- [ ] **Step 1: Add report to STAGE_SCRIPTS**

Replace:
```python
STAGE_SCRIPTS = {
    "per_file": "src/nano_strix/agents/per_file.py",
    "cross_file": "src/nano_strix/agents/cross_file.py",
    "exploit": "src/nano_strix/agents/exploit.py",
}
```

With:
```python
STAGE_SCRIPTS = {
    "per_file": "src/nano_strix/agents/per_file.py",
    "cross_file": "src/nano_strix/agents/cross_file.py",
    "exploit": "src/nano_strix/agents/exploit.py",
    "report": "src/nano_strix/agents/report.py",
}
```

- [ ] **Step 2: Run runner test**

Run: `.venv/bin/pytest tests/test_orchestrator_runner.py -v`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add src/nano_strix/orchestrator/runner.py
git commit -m "feat: add report stage to STAGE_SCRIPTS mapping

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 3: StageScheduler 新增 resume_task() 方法

**Files:**
- Modify: `src/nano_strix/orchestrator/scheduler.py`
- Test: `tests/test_scheduler.py`

- [ ] **Step 1: Write failing test for resume_task**

Add to `tests/test_scheduler.py`:

```python
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

    # Resume should put task into report stage queue
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
    # Verify no new task was enqueued by checking _remaining wasn't incremented
    assert scheduler._remaining == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_scheduler.py::test_scheduler_resume_task_from_second_stage -v`
Expected: FAIL with "StageScheduler has no attribute 'resume_task'"

- [ ] **Step 3: Implement resume_task() in scheduler.py**

Add to `StageScheduler` class, after `submit_batch()` method (after line 65):

```python
    async def resume_task(self, task_id: str, target_path: str) -> None:
        state = self._event_bus.get_state(task_id)
        for stage in self._stages:
            if stage not in state.stage_results:
                self._remaining += 1
                await self._queues[stage].put((task_id, target_path))
                return
```

- [ ] **Step 4: Run resume tests**

Run: `.venv/bin/pytest tests/test_scheduler.py::test_scheduler_resume_task_from_second_stage tests/test_scheduler.py::test_scheduler_resume_task_already_completed -v`
Expected: Both PASS

- [ ] **Step 5: Run all scheduler tests to verify no regression**

Run: `.venv/bin/pytest tests/test_scheduler.py -v`
Expected: All PASS (existing 6 tests + 2 new tests = 8 tests)

- [ ] **Step 6: Commit**

```bash
git add src/nano_strix/orchestrator/scheduler.py tests/test_scheduler.py
git commit -m "feat: add resume_task() to StageScheduler for breakpoint recovery

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 4: _execute_pipeline() 辅助函数

**Files:**
- Modify: `src/nano_strix/cli.py`

- [ ] **Step 1: Add _execute_pipeline() to cli.py**

Add after the imports and before the Click group definition (after line 8). Add the needed imports first. Change the existing import block from:

```python
from pathlib import Path

import click

from nano_strix.config.loader import load_config
from nano_strix.config.paths import DEFAULT_CONFIG_PATH
from nano_strix.config.schema import AppConfig
```

To:

```python
import asyncio
import logging
from pathlib import Path

import click

from nano_strix.agents.manager import AgentManager
from nano_strix.bus.queue import EventBus
from nano_strix.config.loader import load_config
from nano_strix.config.paths import DEFAULT_CONFIG_PATH
from nano_strix.config.schema import AppConfig
from nano_strix.orchestrator.scheduler import StageScheduler

logger = logging.getLogger(__name__)
```

Then add `_execute_pipeline()` before `@click.group()`:

```python
async def _execute_pipeline(
    workspace: Path,
    config: AppConfig,
    targets: list[str],
    stages: list[str],
    input_overrides: dict[str, str] | None = None,
    verbose: bool = False,
) -> list[str]:
    config.pipeline.stages = stages
    if input_overrides:
        config.pipeline.input_overrides = input_overrides

    tasks_dir = workspace / "tasks"
    event_bus = EventBus(tasks_dir)
    agent_manager = AgentManager(workspace=workspace, config=config.ipc)
    scheduler = StageScheduler(
        workspace=workspace,
        config=config,
        agent_manager=agent_manager,
        event_bus=event_bus,
    )

    click.echo(f"Targets: {len(targets)}")
    click.echo(f"Pipeline: {' -> '.join(stages)}")
    if verbose:
        for stage_name, sc in config.scheduler.stages.items():
            click.echo(
                f"  {stage_name}: max_concurrent={sc.max_concurrent}, "
                f"max_retries={sc.max_retries}"
            )

    task_ids = await scheduler.submit_batch(targets)
    click.echo(f"Submitted {len(task_ids)} tasks")
    await scheduler.run()

    failed_count = 0
    for tid in task_ids:
        state = event_bus.get_state(tid)
        status_label = state.status.upper()
        if state.status == "failed":
            failed_count += 1
            click.echo(f"  [{status_label}] {tid}: {state.error or 'unknown error'}")
        else:
            click.echo(f"  [{status_label}] {tid}")

    if failed_count > 0:
        click.echo(
            f"\n{failed_count} task(s) failed. "
            f"Use 'nano-strix resume <task_id>' to retry."
        )

    return task_ids
```

- [ ] **Step 2: Run existing CLI tests to verify no import errors**

Run: `.venv/bin/pytest tests/test_cli.py -v`
Expected: PASS (no tests call `run` or `run-batch` yet, just help text and other commands)

- [ ] **Step 3: Commit**

```bash
git add src/nano_strix/cli.py
git commit -m "feat: add _execute_pipeline() helper for shared pipeline execution

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 5: 重写 run 命令

**Files:**
- Modify: `src/nano_strix/cli.py`

- [ ] **Step 1: Replace the existing run command implementation**

Replace the entire `run` function (from `@main.command()` through the end of the function body, lines 69-119) with:

```python
@main.command()
@click.option(
    "--target",
    required=False,
    type=click.Path(exists=True),
    help="Target code directory",
)
@click.option(
    "--targets-file",
    type=click.Path(exists=True),
    help="File with one target path per line",
)
@click.option(
    "--pipeline",
    default="full",
    help="Pipeline preset or comma-separated stages",
)
@click.option(
    "--input",
    "input_overrides",
    multiple=True,
    help="Input overrides (key=path)",
)
@click.option("--config", "config_path", type=click.Path(), help="Config file path")
@click.option("--model", help="Override default model")
@click.option("--output", type=click.Path(), help="Output directory")
@click.option("--verbose", is_flag=True, help="Verbose logging")
@click.option("--no-snapshot", is_flag=True, help="Analyze target in-place (no copy)")
def run(
    target, targets_file, pipeline, input_overrides, config_path, model, output, verbose, no_snapshot
):
    """Run a penetration test pipeline."""
    if not target and not targets_file:
        raise click.UsageError("Either --target or --targets-file must be provided.")

    cfg = load_config(Path(config_path) if config_path else DEFAULT_CONFIG_PATH)
    if model:
        cfg.llm.model = model

    pipeline_presets = {
        "full": ["per_file", "cross_file", "exploit", "report"],
        "analysis": ["per_file", "cross_file", "report"],
        "exploit_only": ["exploit", "report"],
        "quick": ["per_file", "report"],
    }

    if pipeline in pipeline_presets:
        stages = pipeline_presets[pipeline]
    else:
        stages = [s.strip() for s in pipeline.split(",")]

    targets = []
    if target:
        targets.append(target)
    if targets_file:
        targets_path = Path(targets_file)
        for line in targets_path.read_text().splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#"):
                targets.append(stripped)

    if not targets:
        raise click.UsageError("No targets found.")

    overrides = {}
    for item in input_overrides:
        key, _, path = item.partition("=")
        overrides[key] = path

    workspace = Path(output) if output else Path.cwd()
    workspace.mkdir(parents=True, exist_ok=True)

    if verbose:
        logging.basicConfig(level=logging.DEBUG)

    asyncio.run(_execute_pipeline(
        workspace=workspace,
        config=cfg,
        targets=targets,
        stages=stages,
        input_overrides=overrides,
        verbose=verbose,
    ))
```

- [ ] **Step 2: Run CLI help test to verify command still works**

Run: `.venv/bin/pytest tests/test_cli.py::test_cli_run_help -v`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add src/nano_strix/cli.py
git commit -m "feat: rewrite run command to execute pipeline via _execute_pipeline()

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 6: 重写 run-batch 命令

**Files:**
- Modify: `src/nano_strix/cli.py`

- [ ] **Step 1: Replace existing run_batch function**

Replace the entire `run_batch` function (from `@main.command("run-batch")` through end of function body, lines 142-200) with:

```python
@main.command("run-batch")
@click.argument("targets_file", type=click.Path(exists=True))
@click.option("--config", "config_path", type=click.Path(), help="Config file path")
@click.option("--model", help="Override default model")
@click.option("--output", type=click.Path(), help="Output directory")
@click.option("--verbose", is_flag=True, help="Verbose logging")
def run_batch(targets_file, config_path, model, output, verbose):
    """Run pipeline on multiple targets from a file (one path per line)."""
    cfg = load_config(Path(config_path) if config_path else DEFAULT_CONFIG_PATH)
    if model:
        cfg.llm.model = model

    targets_path = Path(targets_file)
    targets = [
        line.strip()
        for line in targets_path.read_text().splitlines()
        if line.strip() and not line.startswith("#")
    ]

    if not targets:
        raise click.UsageError("No targets found in file.")

    workspace = Path(output) if output else Path.cwd()
    workspace.mkdir(parents=True, exist_ok=True)

    if verbose:
        logging.basicConfig(level=logging.DEBUG)

    asyncio.run(_execute_pipeline(
        workspace=workspace,
        config=cfg,
        targets=targets,
        stages=cfg.pipeline.stages,
        verbose=verbose,
    ))
```

- [ ] **Step 2: Run CLI help test**

Run: `.venv/bin/pytest tests/test_cli.py::test_cli_run_batch_help -v`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add src/nano_strix/cli.py
git commit -m "feat: rewrite run-batch to delegate to _execute_pipeline()

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 7: 重写 resume 命令

**Files:**
- Modify: `src/nano_strix/cli.py`

- [ ] **Step 1: Replace existing resume function**

Replace the existing `resume` function (lines 122-126) with:

```python
@main.command()
@click.argument("task_id")
@click.option("--config", "config_path", type=click.Path(), help="Config file path")
@click.option("--output", type=click.Path(), help="Output directory (workspace)")
def resume(task_id, config_path, output):
    """Resume an interrupted task."""
    cfg = load_config(Path(config_path) if config_path else DEFAULT_CONFIG_PATH)

    workspace = Path(output) if output else Path.cwd()
    tasks_dir = workspace / "tasks"

    if not (tasks_dir / task_id / "state.json").exists():
        raise click.ClickException(f"Task not found: {task_id}")

    event_bus = EventBus(tasks_dir)
    state = event_bus.get_state(task_id)

    # Check if already fully completed
    incomplete_stages = [
        s for s in state.stages if s not in state.stage_results
    ]
    if not incomplete_stages:
        click.echo(f"Task {task_id} already completed.")
        return

    # Extract target path from task_created event
    events = event_bus.get_events(task_id)
    target_path = None
    for ev in events:
        if ev.event_type == "task_created":
            target_path = ev.payload.get("target")
            break

    if not target_path:
        target_path = "unknown"

    click.echo(
        f"Resuming task {task_id}: "
        f"remaining stages: {' -> '.join(incomplete_stages)}"
    )

    agent_manager = AgentManager(workspace=workspace, config=cfg.ipc)
    scheduler = StageScheduler(
        workspace=workspace,
        config=cfg,
        agent_manager=agent_manager,
        event_bus=event_bus,
    )

    async def _resume():
        await scheduler.resume_task(task_id, target_path)
        await scheduler.run()
        final_state = event_bus.get_state(task_id)
        status_label = final_state.status.upper()
        if final_state.status == "failed":
            click.echo(f"  [{status_label}] {task_id}: {final_state.error or 'unknown error'}")
        else:
            click.echo(f"  [{status_label}] {task_id}")

    asyncio.run(_resume())
```

- [ ] **Step 2: Remove the report command**

Remove the entire `report` function block (lines 129-139 in the original file):

```python
@main.command()
@click.argument("task_id")
@click.option(
    "--format",
    "fmt",
    default="markdown",
    type=click.Choice(["markdown", "html", "pdf"]),
)
def report(task_id, fmt):
    """Regenerate report from existing results."""
    click.echo(f"Generating {fmt} report for task {task_id}...")
```

- [ ] **Step 3: Run all CLI tests**

Run: `.venv/bin/pytest tests/test_cli.py -v`
Expected: All PASS (existing tests for help text, version, config)

- [ ] **Step 4: Commit**

```bash
git add src/nano_strix/cli.py
git commit -m "feat: rewrite resume command, remove report command

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 8: 端到端集成测试

**Files:**
- Modify: `tests/test_cli.py`

- [ ] **Step 1: Add integration test for run command**

Add to `tests/test_cli.py`:

```python
def test_cli_run_with_target(tmp_path):
    target_dir = tmp_path / "target"
    target_dir.mkdir()
    (target_dir / "app.py").write_text("print('hello')")

    workspace = tmp_path / "output"

    runner = CliRunner()
    result = runner.invoke(main, [
        "run",
        "--target", str(target_dir),
        "--output", str(workspace),
        "--pipeline", "quick",
    ])
    assert result.exit_code == 0
    assert "Submitted" in result.output
    assert "COMPLETED" in result.output


def test_cli_run_missing_target():
    runner = CliRunner()
    result = runner.invoke(main, ["run", "--pipeline", "quick"])
    assert result.exit_code != 0
    assert "target" in result.output.lower()


def test_cli_run_batch_with_file(tmp_path):
    target1 = tmp_path / "t1"
    target1.mkdir()
    target2 = tmp_path / "t2"
    target2.mkdir()

    targets_file = tmp_path / "targets.txt"
    targets_file.write_text(f"{target1}\n{target2}\n")

    workspace = tmp_path / "output"

    runner = CliRunner()
    result = runner.invoke(main, [
        "run-batch",
        str(targets_file),
        "--output", str(workspace),
    ])
    assert result.exit_code == 0
    assert "Submitted 2" in result.output


def test_cli_resume_nonexistent_task(tmp_path):
    workspace = tmp_path / "output"
    workspace.mkdir()

    runner = CliRunner()
    result = runner.invoke(main, [
        "resume",
        "t-nonexistent",
        "--output", str(workspace),
    ])
    assert result.exit_code != 0
    assert "not found" in result.output.lower()
```

- [ ] **Step 2: Run all tests**

Run: `.venv/bin/pytest tests/test_cli.py -v`
Expected: 4 new tests PASS + existing tests PASS

- [ ] **Step 3: Run full test suite**

Run: `.venv/bin/pytest -v`
Expected: All tests PASS

- [ ] **Step 4: Commit**

```bash
git add tests/test_cli.py
git commit -m "test: add integration tests for run, run-batch, and resume commands

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 9: 手动验证

- [ ] **Step 1: Run run command with a dummy target**

```bash
mkdir -p /tmp/test-target && echo "print('hello')" > /tmp/test-target/app.py
.venv/bin/nano-strix run --target /tmp/test-target --output /tmp/nano-strix-out --pipeline quick
```

Expected output: Agent log messages for per_file and report stages, followed by "COMPLETED" status.

- [ ] **Step 2: Verify state.json is written**

```bash
ls /tmp/nano-strix-out/tasks/t-*/state.json
```

Expected: state.json file exists with completed status and stage_results.

- [ ] **Step 3: Run resume on an already completed task**

```bash
TASK_ID=$(ls /tmp/nano-strix-out/tasks/ | head -1)
.venv/bin/nano-strix resume $TASK_ID --output /tmp/nano-strix-out
```

Expected: "Task ... already completed."

- [ ] **Step 4: Clean up**

```bash
rm -rf /tmp/test-target /tmp/nano-strix-out
```
