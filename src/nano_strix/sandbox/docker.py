from __future__ import annotations

from pathlib import Path

from nano_strix.sandbox.base import ExecutionResult, Sandbox, SandboxConfig


class DockerSandbox(Sandbox):
    """Docker-based sandbox for exploit verification. Not yet implemented."""

    def __init__(self, config: SandboxConfig, workspace: Path) -> None:
        self._config = config
        self.workspace = workspace

    async def execute(
        self, command: str, timeout: int | None = None
    ) -> ExecutionResult:
        raise NotImplementedError("DockerSandbox not yet implemented")

    async def copy_in(self, local_path: str, sandbox_path: str) -> None:
        raise NotImplementedError

    async def copy_out(self, sandbox_path: str, local_path: str) -> None:
        raise NotImplementedError

    async def destroy(self) -> None:
        raise NotImplementedError
