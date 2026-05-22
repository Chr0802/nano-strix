from __future__ import annotations

from contextvars import ContextVar
from typing import Any

current_agent_id: ContextVar[str] = ContextVar("current_agent_id", default="default")
current_sandbox: ContextVar[Any] = ContextVar("current_sandbox", default=None)


def get_current_agent_id() -> str:
    return current_agent_id.get()


def set_current_agent_id(agent_id: str) -> None:
    current_agent_id.set(agent_id)


def get_current_sandbox() -> Any:
    """Return the current DockerSandbox instance, or None if not set."""
    return current_sandbox.get()


def set_current_sandbox(sandbox: Any) -> None:
    """Set the current DockerSandbox for tool routing."""
    current_sandbox.set(sandbox)
