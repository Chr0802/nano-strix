from __future__ import annotations

import asyncio
import json as _json
import logging
import sys
from pathlib import Path
from typing import Any

from nano_strix.agents.base import IPCMessage
from nano_strix.config.schema import IPCConfig

logger = logging.getLogger(__name__)

# Time between heartbeats before considering the subprocess stuck.
# Heartbeat interval is 30s in the stage script; we allow 120s (4 intervals).
_NO_PROGRESS_TIMEOUT = 120


class AgentManager:
    def __init__(self, workspace: Path, config: IPCConfig) -> None:
        self._workspace = workspace
        self._config = config

    async def dispatch(
        self,
        agent_script: str,
        task_id: str,
        stage: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        message = IPCMessage(type="task", task_id=task_id, stage=stage, payload=payload)

        logger.info("Dispatching %s agent: task_id=%s target=%s",
                     stage, task_id, payload.get("target", "?"))

        process = await asyncio.create_subprocess_exec(
            sys.executable,
            agent_script,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(self._workspace),
        )

        try:
            # Send input message and close stdin
            process.stdin.write(message.to_json().encode() + b"\n")
            await process.stdin.drain()
            process.stdin.close()

            # Background task to drain stderr (prevent pipe blocking)
            async def _drain_stderr() -> bytes:
                return await process.stderr.read()

            stderr_task = asyncio.create_task(_drain_stderr())

            # Read stdout line by line; heartbeat resets progress timer
            result_msg: IPCMessage | None = None

            while True:
                try:
                    line_bytes = await asyncio.wait_for(
                        process.stdout.readline(),
                        timeout=_NO_PROGRESS_TIMEOUT,
                    )
                except asyncio.TimeoutError:
                    logger.error(
                        "%s agent: no progress for %ds, killing",
                        stage, _NO_PROGRESS_TIMEOUT,
                    )
                    process.kill()
                    await process.wait()
                    return {
                        "error": f"No progress for {_NO_PROGRESS_TIMEOUT}s — agent may be stuck",
                    }

                if not line_bytes:
                    break  # EOF — subprocess exited

                line_str = line_bytes.decode(errors="replace").strip()
                if not line_str:
                    continue

                try:
                    parsed = _json.loads(line_str)
                except Exception:
                    continue

                if parsed.get("type") == "heartbeat":
                    logger.debug(
                        "[%s heartbeat] iteration=%s agents=%s",
                        stage,
                        parsed.get("iteration"),
                        parsed.get("agent_count"),
                    )
                    continue

                # Non-heartbeat JSON line → final result
                result_msg = IPCMessage.from_json(line_str)
                break

            # Collect stderr
            stderr_task.cancel()
            try:
                stderr_bytes = await stderr_task
            except asyncio.CancelledError:
                stderr_bytes = b""

            stderr_text = stderr_bytes.decode(errors="replace").strip()
            if stderr_text:
                for line in stderr_text.splitlines():
                    logger.debug("[%s stderr] %s", stage, line)

            if result_msg is None:
                # Process exited without sending a result
                if process.returncode != 0:
                    logger.error(
                        "%s agent failed: returncode=%d stderr=%s",
                        stage, process.returncode,
                        stderr_text[:500] if stderr_text else "(empty)",
                    )
                    return {"error": stderr_text or f"Agent exited with code {process.returncode}"}
                logger.error("%s agent produced no result", stage)
                return {"error": "Agent produced no result"}

            logger.info("%s agent completed: task_id=%s status=%s",
                        stage, task_id, result_msg.payload.get("status", "?"))
            return result_msg.payload

        except Exception:
            process.kill()
            await process.wait()
            raise
