from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


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
