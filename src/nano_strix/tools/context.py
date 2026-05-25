from __future__ import annotations

from contextvars import ContextVar
from pathlib import Path
from typing import Any

current_agent_id: ContextVar[str] = ContextVar("current_agent_id", default="default")
current_sandbox: ContextVar[Any] = ContextVar("current_sandbox", default=None)
current_workspace_root: ContextVar[str | None] = ContextVar("current_workspace_root", default=None)


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


def get_current_workspace_root() -> str | None:
    """Return the current workspace root path, or None if not set."""
    return current_workspace_root.get()


def set_current_workspace_root(root: str) -> None:
    """Set the workspace root for path restriction enforcement.

    When set, file_ops tools (file_read, directory_list, file_search,
    file_write) will reject paths outside this directory.
    """
    current_workspace_root.set(root)


current_agent_state: ContextVar[Any] = ContextVar("current_agent_state", default=None)


def get_current_agent_state() -> Any:
    """Return the current agent's AgentState, or None if not in an agent context."""
    return current_agent_state.get()


def set_current_agent_state(state: Any) -> None:
    """Set the current agent's AgentState for graph tool injection."""
    current_agent_state.set(state)


def resolve_and_validate_path(path: str) -> Path:
    """Resolve *path* and verify it is within the current workspace root.

    Relative paths are resolved against the workspace root.  Absolute
    paths are checked to ensure they fall inside the root.  Symlinks are
    resolved to prevent escape via indirection.

    Returns the resolved absolute ``Path`` on success.
    Raises ``PermissionError`` if the path is outside the root.
    Raises ``RuntimeError`` if no workspace root has been configured.
    """
    root_str = current_workspace_root.get()
    if root_str is None:
        raise RuntimeError("No workspace root configured — cannot validate path")

    root = Path(root_str).resolve()
    p = Path(path)

    if not p.is_absolute():
        p = root / p

    resolved = p.resolve()

    # Ensure the resolved path is within the workspace root
    try:
        resolved.relative_to(root)
    except ValueError:
        raise PermissionError(
            f"Access denied: '{path}' resolves outside workspace root '{root}'"
        )

    return resolved
