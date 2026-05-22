# src/nano_strix/sandbox/tool_models.py
"""Pydantic models shared between tool server and DockerSandbox client."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class TerminalExecuteRequest(BaseModel):
    command: str
    timeout: int = 30
    cwd: str = "/workspace/source"


class FileReadRequest(BaseModel):
    path: str


class ScannerRequest(BaseModel):
    target: str = "/workspace/source"
    extra_args: list[str] = Field(default_factory=list)


class ToolExecutionRequest(BaseModel):
    tool_name: str
    kwargs: dict[str, Any] = Field(default_factory=dict)
    timeout: int = 120


class ToolExecutionResponse(BaseModel):
    result: Any | None = None
    error: str | None = None
    exit_code: int = 0
    stdout: str = ""
    stderr: str = ""


class HealthResponse(BaseModel):
    status: str
    sandbox_mode: bool
    auth_configured: bool
    active_tools: list[str]
