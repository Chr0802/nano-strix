from __future__ import annotations

import asyncio
import shutil
import time
from pathlib import Path

from nano_strix.config.schema import SandboxConfig
from nano_strix.sandbox.base import ExecutionResult, Sandbox


class ProcessSandbox(Sandbox):
    def __init__(self, config: SandboxConfig, workspace: Path) -> None:
        self._config = config
        self.workspace = workspace
        self.workspace.mkdir(parents=True, exist_ok=True)

    async def execute(
        self, command: str, timeout: int | None = None
    ) -> ExecutionResult:
        effective_timeout = timeout or self._config.timeout
        start = time.monotonic()

        process = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(self.workspace),
            env={**self._config.env_vars} if self._config.env_vars else None,
        )

        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(), timeout=effective_timeout
            )
            duration = time.monotonic() - start
            return ExecutionResult(
                exit_code=process.returncode or 0,
                stdout=stdout.decode(errors="replace"),
                stderr=stderr.decode(errors="replace"),
                duration=duration,
            )
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
            duration = time.monotonic() - start
            return ExecutionResult(
                exit_code=-1,
                stdout="",
                stderr=f"Command timed out after {effective_timeout}s",
                duration=duration,
            )

    async def copy_in(self, local_path: str, sandbox_path: str) -> None:
        dest = Path(sandbox_path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(local_path, dest)

    async def copy_out(self, sandbox_path: str, local_path: str) -> None:
        dest = Path(local_path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(sandbox_path, dest)

    async def destroy(self) -> None:
        pass  # Nothing to clean up for process sandbox
