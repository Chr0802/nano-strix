from __future__ import annotations

import asyncio
import logging
import shutil
from pathlib import Path
from typing import Any

from nano_strix.agents.manager import AgentManager
from nano_strix.bus.events import TaskEvent, TaskState
from nano_strix.bus.queue import EventBus
from nano_strix.config.schema import AppConfig
from nano_strix.orchestrator.runner import STAGE_SCRIPTS

logger = logging.getLogger(__name__)

_SENTINEL = object()


class StageScheduler:
    def __init__(
        self,
        workspace: Path,
        config: AppConfig,
        agent_manager: AgentManager,
        event_bus: EventBus,
    ) -> None:
        self._workspace = workspace
        self._config = config
        self._agent_manager = agent_manager
        self._event_bus = event_bus
        self._stages = config.pipeline.stages
        self._no_snapshot = False

        self._queues: dict[str, asyncio.Queue] = {}
        self._stage_configs = config.scheduler.stages

        for stage in self._stages:
            self._queues[stage] = asyncio.Queue()

        self._remaining = 0
        self._all_done: asyncio.Event | None = None
        self._shutdown = False

    # ---- Task workspace helpers ----

    def _task_dir(self, task_id: str) -> Path:
        """Return the task-level workspace directory."""
        return self._event_bus._root / task_id

    def _prepare_task_source(
        self, task_id: str, original_target: str
    ) -> Path:
        """Ensure the target source is available in the task workspace.

        Returns the path to the source directory inside the task workspace.
        When ``no_snapshot`` is True, returns the original target path as-is.
        When the target does not exist on disk, creates an empty source
        directory (used in tests).
        """
        target_path = Path(original_target).resolve()
        task_dir = self._task_dir(task_id)

        if self._no_snapshot:
            logger.info(
                "Task %s: no-snapshot mode, using original target %s",
                task_id, target_path,
            )
            return target_path

        source_dir = task_dir / "source"
        if source_dir.exists():
            logger.info(
                "Task %s: source directory already exists at %s",
                task_id, source_dir,
            )
            return source_dir

        source_dir.mkdir(parents=True, exist_ok=True)

        if not target_path.exists():
            logger.warning(
                "Task %s: target does not exist (%s), "
                "creating empty source directory",
                task_id, target_path,
            )
            return source_dir

        logger.info(
            "Task %s: copying target from %s to %s ...",
            task_id, target_path, source_dir,
        )
        _copy_tree(target_path, source_dir)
        logger.info(
            "Task %s: copy complete (%s -> %s)",
            task_id, target_path, source_dir,
        )
        return source_dir

    # ---- Task submission ----

    async def submit_task(self, target_path: str) -> str:
        state = self._event_bus.create_task(self._stages)
        self._event_bus.publish(
            TaskEvent(
                task_id=state.task_id,
                event_type="task_created",
                stage=None,
                payload={
                    "target": target_path,
                    "no_snapshot": self._no_snapshot,
                },
            )
        )
        self._remaining += 1
        await self._queues[self._stages[0]].put((state.task_id, target_path))
        return state.task_id

    async def submit_batch(
        self, targets: list[str], *, no_snapshot: bool = False,
    ) -> list[str]:
        self._no_snapshot = no_snapshot
        task_ids = []
        for target in targets:
            task_id = await self.submit_task(target)
            task_ids.append(task_id)
        return task_ids

    async def resume_task(self, task_id: str, target_path: str) -> None:
        state = self._event_bus.get_state(task_id)
        if state.status == "running":
            raise RuntimeError(f"Task {task_id} is already running")

        # Restore no_snapshot from task_created event
        events = self._event_bus.get_events(task_id)
        for ev in events:
            if ev.event_type == "task_created":
                self._no_snapshot = ev.payload.get("no_snapshot", False)
                break

        for stage in self._stages:
            if stage not in state.stage_results:
                state.status = "pending"
                state.error = None
                self._event_bus.update_state(state)
                self._remaining += 1
                await self._queues[stage].put((task_id, target_path))
                return

    # ---- Pipeline execution ----

    async def run(self) -> None:
        self._all_done = asyncio.Event()
        self._shutdown = False
        if self._remaining == 0:
            return

        workers = []
        for i, stage in enumerate(self._stages):
            next_stage = self._stages[i + 1] if i + 1 < len(self._stages) else None
            concurrency = self._stage_configs.get(stage)
            max_c = concurrency.max_concurrent if concurrency else 1
            for _ in range(max_c):
                workers.append(
                    asyncio.create_task(
                        self._run_stage_worker(stage, next_stage)
                    )
                )
        await asyncio.gather(*workers)

    def _mark_done(self) -> None:
        self._remaining -= 1
        if self._remaining <= 0 and not self._shutdown:
            self._shutdown = True
            for stage in self._stages:
                concurrency = self._stage_configs.get(stage)
                n = concurrency.max_concurrent if concurrency else 1
                for _ in range(n):
                    self._queues[stage].put_nowait(_SENTINEL)
            if self._all_done is not None:
                self._all_done.set()

    async def _run_stage_worker(
        self, stage: str, next_stage: str | None,
    ) -> None:
        queue = self._queues[stage]

        while True:
            if queue.empty():
                if self._remaining <= 0:
                    break
                await asyncio.sleep(0.01)
                continue

            item = await queue.get()
            if item is _SENTINEL:
                queue.task_done()
                break

            task_id, original_target = item
            queue.task_done()

            state = self._event_bus.get_state(task_id)
            if state.status == "failed":
                self._mark_done()
                continue

            await self._execute_stage(state, stage, original_target)

            state = self._event_bus.get_state(task_id)
            if state.status == "failed":
                self._mark_done()
                continue

            if next_stage is not None:
                await self._queues[next_stage].put(
                    (task_id, original_target)
                )
            else:
                state.status = "completed"
                self._event_bus.update_state(state)
                self._event_bus.publish(
                    TaskEvent(
                        task_id=task_id,
                        event_type="task_completed",
                        stage=None,
                    )
                )
                self._mark_done()

    async def _execute_stage(
        self, state: TaskState, stage: str, original_target: str,
    ) -> dict[str, Any]:
        stage_conf = self._stage_configs.get(stage)
        max_retries = stage_conf.max_retries if stage_conf else 2

        # Prepare task workspace source copy before first stage execution.
        target_in_workspace = self._prepare_task_source(
            state.task_id, original_target,
        )
        task_workspace = self._task_dir(state.task_id)

        stage_payload = {
            "target": str(target_in_workspace),
            "workspace": str(task_workspace),
            "stage_results": state.stage_results,
        }

        state.advance(stage)
        self._event_bus.update_state(state)
        self._event_bus.publish(
            TaskEvent(
                task_id=state.task_id,
                event_type="stage_started",
                stage=stage,
                payload=stage_payload,
            )
        )

        agent_script = STAGE_SCRIPTS.get(stage)
        if not agent_script:
            error = f"No agent script for stage: {stage}"
            state.fail(error)
            self._event_bus.update_state(state)
            return {"error": error}

        last_error = None
        for attempt in range(max_retries + 1):
            result = await self._agent_manager.dispatch(
                agent_script=agent_script,
                task_id=state.task_id,
                stage=stage,
                payload=stage_payload,
            )

            if "error" not in result:
                state.complete_stage(stage, result)
                self._event_bus.update_state(state)
                self._event_bus.publish(
                    TaskEvent(
                        task_id=state.task_id,
                        event_type="stage_completed",
                        stage=stage,
                        payload=result,
                    )
                )
                return result

            last_error = result["error"]
            state.retry_counts[stage] = attempt + 1
            self._event_bus.update_state(state)
            logger.warning(
                "Task %s stage %s attempt %d/%d failed: %s",
                state.task_id,
                stage,
                attempt + 1,
                max_retries + 1,
                last_error,
            )

        error_msg = (
            f"Stage {stage} failed after "
            f"{max_retries + 1} attempts: {last_error}"
        )
        state.fail(error_msg)
        self._event_bus.update_state(state)
        self._event_bus.publish(
            TaskEvent(
                task_id=state.task_id,
                event_type="task_failed",
                stage=stage,
                payload={"error": error_msg},
            )
        )
        return {"error": error_msg}


def _copy_tree(src: Path, dst: Path) -> None:
    """Copy directory tree with symlink handling for safe workspace isolation."""
    if not src.is_dir():
        # Single file target
        shutil.copy2(str(src), str(dst / src.name))
        return

    for item in src.iterdir():
        s = src / item.name
        d = dst / item.name
        if s.is_symlink():
            # Skip symlinks to avoid following them outside the target
            continue
        if s.is_dir():
            shutil.copytree(str(s), str(d), symlinks=False)
        else:
            shutil.copy2(str(s), str(d))
