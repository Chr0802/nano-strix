from __future__ import annotations

from pathlib import Path

from nano_strix.sandbox.base import Sandbox, SandboxConfig
from nano_strix.sandbox.process import ProcessSandbox


class SandboxManager:
    """Creates and manages sandbox instances."""

    def __init__(self, workspace: Path) -> None:
        self._workspace = workspace

    def create(self, config: SandboxConfig) -> Sandbox:
        if config.sandbox_type == "process":
            return ProcessSandbox(config, self._workspace)
        elif config.sandbox_type == "docker":
            from nano_strix.sandbox.docker import DockerSandbox

            return DockerSandbox(config, self._workspace)
        else:
            raise ValueError(f"Unknown sandbox type: {config.sandbox_type}")
