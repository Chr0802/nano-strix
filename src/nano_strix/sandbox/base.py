from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class SandboxConfig:
    sandbox_type: str = "process"  # docker / process
    image: str = "python:3.12-slim"
    network: str = "none"
    memory_limit: str = "512m"
    cpu_limit: float = 1.0
    timeout: int = 600
    env_vars: dict[str, str] = field(default_factory=dict)
    volumes: list[dict[str, str]] = field(default_factory=list)


@dataclass
class ExecutionResult:
    exit_code: int
    stdout: str
    stderr: str
    duration: float

    @property
    def success(self) -> bool:
        return self.exit_code == 0


class Sandbox(ABC):
    @abstractmethod
    async def execute(
        self, command: str, timeout: int | None = None
    ) -> ExecutionResult: ...

    @abstractmethod
    async def copy_in(self, local_path: str, sandbox_path: str) -> None: ...

    @abstractmethod
    async def copy_out(self, sandbox_path: str, local_path: str) -> None: ...

    @abstractmethod
    async def destroy(self) -> None: ...
