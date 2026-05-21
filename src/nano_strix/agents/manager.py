from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path
from typing import Any

from nano_strix.agents.base import IPCMessage
from nano_strix.config.schema import IPCConfig

logger = logging.getLogger(__name__)


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
            stdout, stderr = await asyncio.wait_for(
                process.communicate(message.to_json().encode() + b"\n"),
                timeout=self._config.timeout_seconds,
            )

            # Log stderr output (progress messages, logs)
            stderr_text = stderr.decode(errors="replace").strip()
            if stderr_text:
                for line in stderr_text.splitlines():
                    # Pass through agent stderr as-is with stage prefix
                    logger.debug("[%s stderr] %s", stage, line)

            if process.returncode != 0:
                logger.error(
                    "%s agent failed: returncode=%d stderr=%s",
                    stage, process.returncode,
                    stderr_text[:500] if stderr_text else "(empty)",
                )
                return {"error": stderr_text}

            output = stdout.decode(errors="replace").strip()
            if not output:
                logger.error("%s agent produced no stdout output", stage)
                return {"error": "Agent produced no output"}

            result_msg = IPCMessage.from_json(output)
            logger.info("%s agent completed: task_id=%s status=%s",
                        stage, task_id, result_msg.payload.get("status", "?"))
            return result_msg.payload

        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
            logger.error(
                "%s agent timed out after %ds",
                stage, self._config.timeout_seconds,
            )
            return {"error": f"Agent timed out after {self._config.timeout_seconds}s"}
