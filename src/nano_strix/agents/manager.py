from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from nano_strix.agents.base import IPCMessage
from nano_strix.config.schema import IPCConfig


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

        process = await asyncio.create_subprocess_exec(
            "python3",
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

            if process.returncode != 0:
                return {"error": stderr.decode(errors="replace")}

            output = stdout.decode(errors="replace").strip()
            if not output:
                return {"error": "Agent produced no output"}

            result_msg = IPCMessage.from_json(output)
            return result_msg.payload

        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
            return {"error": f"Agent timed out after {self._config.timeout_seconds}s"}
