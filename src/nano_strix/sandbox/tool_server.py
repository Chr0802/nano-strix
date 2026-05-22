"""FastAPI tool server running inside Docker sandbox.

This server is the HTTP API that DeepAnalyseAgent's LLM calls (via
tools/scanner/scanner_actions.py -> DockerSandbox.call_tool_server())
to execute static analysis tools inside the isolated container.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import Depends, FastAPI, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from nano_strix.sandbox.tool_models import (
    HealthResponse,
    ToolExecutionResponse,
)

SANDBOX_MODE = os.getenv("NANO_STRIX_SANDBOX_MODE", "").lower() == "true"

EXPECTED_TOKEN = os.getenv("TOOL_SERVER_TOKEN", "")
REQUEST_TIMEOUT = 120

app = FastAPI(title="nano-strix Tool Server")
security = HTTPBearer(auto_error=False)

# Scanner registry: scanner_name -> (binary, arg_template)
_SCANNER_COMMANDS: dict[str, tuple[str, list[str]]] = {
    "gitleaks": (
        "gitleaks",
        ["detect", "--source", "{target}", "--no-git", "-f", "json"],
    ),
    "trufflehog": ("trufflehog", ["filesystem", "{target}", "--json"]),
    "eslint": ("eslint", ["{target}", "--format", "json"]),
    "retire": ("retire", ["--path", "{target}", "--outputformat", "json"]),
    "jshint": ("jshint", ["{target}", "--reporter", "json"]),
}


def _resolve_cwd(requested_cwd: str) -> str:
    return requested_cwd if Path(requested_cwd).exists() else "/"


def _run_command(
    command: str, timeout: int = 30, cwd: str = "/workspace/source"
) -> dict[str, Any]:
    resolved_cwd = _resolve_cwd(cwd)
    try:
        proc = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=resolved_cwd,
        )
        return {
            "exit_code": proc.returncode,
            "stdout": proc.stdout,
            "stderr": proc.stderr,
        }
    except subprocess.TimeoutExpired:
        return {
            "exit_code": -1,
            "stdout": "",
            "stderr": f"Command timed out after {timeout}s",
        }


def _run_scanner_tool(
    binary: str, args: list[str], timeout: int = 120
) -> dict[str, Any]:
    try:
        proc = subprocess.run(
            [binary, *args],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return {
            "exit_code": proc.returncode,
            "stdout": proc.stdout,
            "stderr": proc.stderr,
        }
    except FileNotFoundError:
        return {
            "exit_code": -1,
            "stdout": "",
            "stderr": "",
            "error": f"{binary} not installed in sandbox",
        }
    except subprocess.TimeoutExpired:
        return {
            "exit_code": -1,
            "stdout": "",
            "stderr": "",
            "error": f"{binary} timed out after {timeout}s",
        }


# ---- Backward-Compatible Routes (lenient body parsing for compat) ----


@app.post("/tools/terminal_execute")
async def handle_terminal(raw_request: Request) -> dict[str, Any]:
    try:
        body = await raw_request.json()
    except Exception:
        body = {}
    command = body.get("command", "")
    timeout = body.get("timeout", 30)
    cwd = body.get("cwd", "/workspace/source")
    return _run_command(command=command, timeout=timeout, cwd=cwd)


@app.post("/tools/file_read")
async def handle_file_read(raw_request: Request) -> dict[str, Any]:
    try:
        body = await raw_request.json()
    except Exception:
        body = {}
    path = body.get("path", "")
    full_path = Path(path)
    if not full_path.is_absolute():
        full_path = Path("/workspace/source") / path
    try:
        content = full_path.read_text(errors="replace")
        return {"content": content, "size": len(content)}
    except Exception as e:
        return {"error": str(e), "content": "", "size": 0}


@app.post("/tools/scanner/semgrep")
async def handle_semgrep(raw_request: Request) -> dict[str, Any]:
    try:
        body = await raw_request.json()
    except Exception:
        body = {}
    target = body.get("target", "/workspace/source")
    extra_args = body.get("extra_args", [])
    return _run_scanner_tool(
        "semgrep",
        ["--config", "auto", "--json", "--no-git-ignore", *extra_args, target],
        timeout=120,
    )


@app.post("/tools/scanner/bandit")
async def handle_bandit(raw_request: Request) -> dict[str, Any]:
    try:
        body = await raw_request.json()
    except Exception:
        body = {}
    target = body.get("target", "/workspace/source")
    extra_args = body.get("extra_args", [])
    return _run_scanner_tool(
        "bandit",
        ["-r", "-f", "json", *extra_args, target],
        timeout=120,
    )


# ---- Extensible Scanner Route ----


@app.post("/tools/scanner/{scanner_name}")
async def handle_scanner(scanner_name: str, raw_request: Request) -> dict[str, Any]:
    entry = _SCANNER_COMMANDS.get(scanner_name)
    if entry is None:
        return {
            "exit_code": -1,
            "stdout": "",
            "stderr": "",
            "error": f"Unknown scanner: {scanner_name}",
        }

    try:
        body = await raw_request.json()
    except Exception:
        body = {}
    target = body.get("target", "/workspace/source")
    extra_args = body.get("extra_args", [])

    binary, arg_template = entry
    resolved_args = [a.replace("{target}", target) for a in arg_template]
    resolved_args.extend(extra_args)
    return _run_scanner_tool(binary, resolved_args, timeout=120)


# ---- Generic Execution Endpoint (optional auth) ----


@app.post("/execute", response_model=ToolExecutionResponse)
async def execute_tool(
    raw_request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
) -> ToolExecutionResponse:
    # Parse raw body to support invalid JSON (backward compatibility)
    try:
        body = await raw_request.json()
    except Exception:
        return ToolExecutionResponse(error="Invalid JSON body", exit_code=-1)

    if EXPECTED_TOKEN:
        if credentials is None or credentials.scheme != "Bearer":
            return ToolExecutionResponse(error="Authentication required")
        if credentials.credentials != EXPECTED_TOKEN:
            return ToolExecutionResponse(error="Invalid authentication token")

    tool_name = body.get("tool_name", "")
    kwargs = body.get("kwargs", {})
    timeout = body.get("timeout", REQUEST_TIMEOUT)

    try:
        if tool_name == "terminal_execute":
            result = _run_command(
                command=kwargs.get("command", ""),
                timeout=kwargs.get("timeout", timeout),
                cwd=kwargs.get("cwd", "/workspace/source"),
            )
            return ToolExecutionResponse(
                exit_code=result["exit_code"],
                stdout=result["stdout"],
                stderr=result["stderr"],
            )

        elif tool_name == "file_read":
            path = kwargs.get("path", "")
            full_path = Path(path)
            if not full_path.is_absolute():
                full_path = Path("/workspace/source") / path
            try:
                content = full_path.read_text(errors="replace")
                return ToolExecutionResponse(
                    result={"content": content, "size": len(content)},
                    exit_code=0,
                )
            except Exception as e:
                return ToolExecutionResponse(error=str(e), exit_code=-1)

        elif tool_name in ("semgrep", "bandit", "gitleaks", "trufflehog", "eslint",
                           "retire", "jshint"):
            target = kwargs.get("target", "/workspace/source")
            if tool_name == "semgrep":
                result = _run_scanner_tool(
                    "semgrep",
                    ["--config", "auto", "--json", "--no-git-ignore", target],
                    timeout=timeout,
                )
            elif tool_name == "bandit":
                result = _run_scanner_tool(
                    "bandit", ["-r", "-f", "json", target], timeout=timeout
                )
            else:
                entry = _SCANNER_COMMANDS.get(tool_name)
                if entry is None:
                    return ToolExecutionResponse(
                        error=f"Unknown tool: {tool_name}", exit_code=-1
                    )
                binary, arg_template = entry
                resolved_args = [
                    a.replace("{target}", target) for a in arg_template
                ]
                result = _run_scanner_tool(binary, resolved_args, timeout=timeout)

            return ToolExecutionResponse(
                exit_code=result["exit_code"],
                stdout=result["stdout"],
                stderr=result["stderr"],
                error=result.get("error"),
            )

        else:
            return ToolExecutionResponse(
                error=f"Unknown tool: {tool_name}", exit_code=-1
            )

    except Exception as e:
        return ToolExecutionResponse(
            error=f"Tool execution error: {e}", exit_code=-1
        )


# ---- Health Check ----


@app.get("/health", response_model=HealthResponse)
async def health_check() -> HealthResponse:
    return HealthResponse(
        status="healthy",
        sandbox_mode=SANDBOX_MODE,
        auth_configured=bool(EXPECTED_TOKEN),
        active_tools=[
            "terminal_execute", "file_read",
            "semgrep", "bandit", *list(_SCANNER_COMMANDS.keys()),
        ],
    )


# ---- Main Entry Point ----


def main():
    global EXPECTED_TOKEN, REQUEST_TIMEOUT

    parser = argparse.ArgumentParser(description="Start nano-strix tool server")
    parser.add_argument("--host", default="0.0.0.0", help="Host to bind to")
    parser.add_argument("--port", type=int, default=8080, help="Port to bind to")
    parser.add_argument("--token", default=None, help="Optional authentication token")
    parser.add_argument(
        "--timeout",
        type=int,
        default=120,
        help="Default timeout in seconds for tool execution (default: 120)",
    )
    cli_args = parser.parse_args()

    if cli_args.token:
        EXPECTED_TOKEN = cli_args.token
    REQUEST_TIMEOUT = cli_args.timeout

    if not SANDBOX_MODE:
        print(
            "WARNING: NANO_STRIX_SANDBOX_MODE is not set to true. "
            "This server should only run inside a sandbox container.",
            file=sys.stderr,
            flush=True,
        )

    uvicorn.run(app, host=cli_args.host, port=cli_args.port, log_level="info")


if __name__ == "__main__":
    main()
