# Docker Sandbox Module Upgrade Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 Docker 沙箱从最小概念验证（BaseHTTPRequestHandler、无扫描工具、host 进程执行）升级为供 deep_agent（ScanAgent）LLM 调用的生产级隔离扫描环境。

**Architecture:** tool server 是运行在 Docker 容器内部的 HTTP API 服务，供 `DeepAnalyseAgent` 的 LLM 通过 `@register_tool` 工具注册机制调用。执行链路：LLM → `tools/scanner/scanner_actions.py`（通过 sandbox context var 判断路由）→ `DockerSandbox.call_tool_server()` → FastAPI tool server → 容器内实际工具执行。当无 sandbox 时回退到 host subprocess。

**注意：** `agents/per_file_lib/scanner.py` 和 `sub_agents.py` 是深度分析重构前的旧实现，已废弃，本次不涉及。

**Tech Stack:** Python 3.10+, FastAPI, uvicorn, Pydantic v2, Docker SDK, aiohttp, semgrep, bandit, gitleaks, trufflehog, eslint

---

## File Structure

| File | Action | Responsibility |
|---|---|---|
| `pyproject.toml` | Modify | 添加 FastAPI/uvicorn/Pydantic 依赖 |
| `src/nano_strix/sandbox/tool_models.py` | Create | Pydantic 请求/响应模型 |
| `src/nano_strix/sandbox/tool_server.py` | Rewrite | FastAPI 应用 — 所有工具端点 |
| `Dockerfile.sandbox` | Rewrite | 带扫描工具的沙箱镜像 |
| `src/nano_strix/sandbox/docker.py` | Modify | 容器生命周期改进 |
| `src/nano_strix/tools/scanner/scanner_actions.py` | Modify | 新增静态扫描工具，支持 sandbox 路由 |
| `src/nano_strix/tools/scanner/scanner_schema.xml` | Modify | 新工具的 XML schema 定义 |
| `src/nano_strix/tools/context.py` | Modify | 新增 `current_sandbox` ContextVar |
| `tests/test_tool_server.py` | Create | FastAPI 路由单元测试 |
| `tests/test_docker_sandbox.py` | Modify | 新增生命周期测试 |

---

### Task 1: 添加 FastAPI 依赖到 pyproject.toml

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: 添加 fastapi, uvicorn, pydantic 到核心依赖**

```toml
# pyproject.toml — 替换 [project] dependencies 块
dependencies = [
    "aiohttp>=3.9",
    "click>=8.0",
    "pyyaml>=6.0",
    "anthropic>=0.40.0",
    "fastapi>=0.100",
    "uvicorn[standard]>=0.23",
    "pydantic>=2.0",
]
```

- [ ] **Step 2: 安装更新的依赖**

```bash
.venv/bin/pip install -e ".[dev]"
```

- [ ] **Step 3: 验证导入可用**

```bash
.venv/bin/python -c "import fastapi; import uvicorn; import pydantic; print('OK')"
```

Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml
git commit -m "feat: add FastAPI, uvicorn, pydantic as core dependencies"
```

---

### Task 2: 创建 Pydantic 模型

**Files:**
- Create: `src/nano_strix/sandbox/tool_models.py`

- [ ] **Step 1: 创建模型文件**

```python
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
```

- [ ] **Step 2: 验证**

```bash
.venv/bin/python -c "from nano_strix.sandbox.tool_models import ToolExecutionRequest, ToolExecutionResponse, HealthResponse; print('OK')"
```

- [ ] **Step 3: Commit**

```bash
git add src/nano_strix/sandbox/tool_models.py
git commit -m "feat: add Pydantic models for tool server API"
```

---

### Task 3: 编写 Tool Server 单元测试

**Files:**
- Create: `tests/test_tool_server.py`

- [ ] **Step 1: 编写测试文件**

```python
# tests/test_tool_server.py
"""Unit tests for FastAPI tool server — no Docker needed."""
import os
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def set_sandbox_mode():
    os.environ["NANO_STRIX_SANDBOX_MODE"] = "true"
    yield
    os.environ.pop("NANO_STRIX_SANDBOX_MODE", None)


@pytest.fixture
def client():
    from nano_strix.sandbox.tool_server import app
    return TestClient(app)


class TestHealthEndpoint:
    def test_health_returns_healthy(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "healthy"
        assert data["sandbox_mode"] is True


class TestTerminalExecute:
    def test_echo_command(self, client):
        resp = client.post(
            "/tools/terminal_execute",
            json={"command": "echo hello-world", "timeout": 10},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["exit_code"] == 0
        assert "hello-world" in data["stdout"]

    def test_timeout(self, client):
        resp = client.post(
            "/tools/terminal_execute",
            json={"command": "sleep 10", "timeout": 1},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["exit_code"] == -1
        assert "timed out" in data["stderr"].lower()

    def test_empty_body_uses_defaults(self, client):
        resp = client.post("/tools/terminal_execute", content=b"{}")
        assert resp.status_code == 200
        data = resp.json()
        assert "exit_code" in data


class TestFileRead:
    def test_existing_file(self, client, tmp_path):
        test_file = tmp_path / "test.txt"
        test_file.write_text("file content here")

        resp = client.post(
            "/tools/file_read",
            json={"path": str(test_file)},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["content"] == "file content here"
        assert data["size"] == 17

    def test_nonexistent_file(self, client):
        resp = client.post(
            "/tools/file_read",
            json={"path": "/nonexistent/path/file.txt"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "error" in data


class TestScannerEndpoints:
    def test_semgrep_not_installed_returns_error(self, client):
        resp = client.post(
            "/tools/scanner/semgrep",
            json={"target": "/tmp"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["exit_code"] == -1
        assert "error" in data

    def test_bandit_not_installed_returns_error(self, client):
        resp = client.post(
            "/tools/scanner/bandit",
            json={"target": "/tmp"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["exit_code"] == -1
        assert "error" in data

    def test_dynamic_route_unknown_scanner(self, client):
        resp = client.post(
            "/tools/scanner/unknown_scanner_xyz",
            json={"target": "/tmp"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "error" in data

    def test_dynamic_route_gitleaks(self, client):
        resp = client.post(
            "/tools/scanner/gitleaks",
            json={"target": "/tmp"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "error" in data or "exit_code" in data


class TestExecuteEndpoint:
    def test_execute_terminal(self, client):
        resp = client.post(
            "/execute",
            json={
                "tool_name": "terminal_execute",
                "kwargs": {"command": "echo from-execute", "timeout": 10},
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["exit_code"] == 0
        assert "from-execute" in data["stdout"]

    def test_execute_unknown_tool(self, client):
        resp = client.post(
            "/execute",
            json={"tool_name": "nonexistent_tool", "kwargs": {}},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["error"] is not None

    def test_execute_no_auth_when_token_not_configured(self, client):
        """Backward-compat: /execute 在未配置 token 时无需鉴权"""
        resp = client.post(
            "/execute",
            json={
                "tool_name": "terminal_execute",
                "kwargs": {"command": "echo no-auth", "timeout": 10},
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["exit_code"] == 0

    def test_execute_invalid_json_body(self, client):
        resp = client.post("/execute", content=b"not valid json")
        assert resp.status_code == 200
        data = resp.json()
        assert data["error"] is not None
```

- [ ] **Step 2: 运行测试确认失败（tool server 尚未重写）**

```bash
.venv/bin/pytest tests/test_tool_server.py -v
```

Expected: 所有测试因 FastAPI app 不存在而失败。

- [ ] **Step 3: Commit**

```bash
git add tests/test_tool_server.py
git commit -m "test: add unit tests for FastAPI tool server routes"
```

---

### Task 4: 用 FastAPI 重写 Tool Server

**Files:**
- Rewrite: `src/nano_strix/sandbox/tool_server.py`

- [ ] **Step 1: 重写 tool server**

```python
# src/nano_strix/sandbox/tool_server.py
"""FastAPI tool server running inside Docker sandbox.

This server is the HTTP API that DeepAnalyseAgent's LLM calls (via
tools/scanner/scanner_actions.py -> DockerSandbox.call_tool_server())
to execute static analysis tools inside the isolated container.
"""
from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import Depends, FastAPI
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from nano_strix.sandbox.tool_models import (
    FileReadRequest,
    HealthResponse,
    ScannerRequest,
    TerminalExecuteRequest,
    ToolExecutionRequest,
    ToolExecutionResponse,
)

SANDBOX_MODE = os.getenv("NANO_STRIX_SANDBOX_MODE", "").lower() == "true"

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
args = parser.parse_args()

EXPECTED_TOKEN = args.token or os.getenv("TOOL_SERVER_TOKEN", "")
REQUEST_TIMEOUT = args.timeout

app = FastAPI(title="nano-strix Tool Server")
security = HTTPBearer(auto_error=False)

# Scanner registry: scanner_name -> (binary, arg_template)
_SCANNER_COMMANDS: dict[str, tuple[str, list[str]]] = {
    "gitleaks": ("gitleaks", ["detect", "--source", "{target}", "--no-git", "-f", "json"]),
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


def _run_scanner_tool(binary: str, args: list[str], timeout: int = 120) -> dict[str, Any]:
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


# ---- Backward-Compatible Routes (no auth required) ----

@app.post("/tools/terminal_execute")
async def handle_terminal(request: TerminalExecuteRequest) -> dict[str, Any]:
    return _run_command(
        command=request.command,
        timeout=request.timeout,
        cwd=request.cwd or "/workspace/source",
    )


@app.post("/tools/file_read")
async def handle_file_read(request: FileReadRequest) -> dict[str, Any]:
    full_path = Path(request.path)
    if not full_path.is_absolute():
        full_path = Path("/workspace/source") / request.path
    try:
        content = full_path.read_text(errors="replace")
        return {"content": content, "size": len(content)}
    except Exception as e:
        return {"error": str(e), "content": "", "size": 0}


@app.post("/tools/scanner/semgrep")
async def handle_semgrep(request: ScannerRequest) -> dict[str, Any]:
    return _run_scanner_tool(
        "semgrep",
        ["--config", "auto", "--json", "--no-git-ignore", *request.extra_args, request.target],
        timeout=120,
    )


@app.post("/tools/scanner/bandit")
async def handle_bandit(request: ScannerRequest) -> dict[str, Any]:
    return _run_scanner_tool(
        "bandit",
        ["-r", "-f", "json", *request.extra_args, request.target],
        timeout=120,
    )


# ---- Extensible Scanner Route ----

@app.post("/tools/scanner/{scanner_name}")
async def handle_scanner(scanner_name: str, request: ScannerRequest) -> dict[str, Any]:
    if scanner_name in ("semgrep", "bandit"):
        pass  # Dedicated handlers above also work here

    entry = _SCANNER_COMMANDS.get(scanner_name)
    if entry is None:
        return {
            "exit_code": -1,
            "stdout": "",
            "stderr": "",
            "error": f"Unknown scanner: {scanner_name}",
        }

    binary, arg_template = entry
    resolved_args = [a.replace("{target}", request.target) for a in arg_template]
    resolved_args.extend(request.extra_args)
    return _run_scanner_tool(binary, resolved_args, timeout=120)


# ---- Generic Execution Endpoint (optional auth) ----

@app.post("/execute", response_model=ToolExecutionResponse)
async def execute_tool(
    request: ToolExecutionRequest,
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
) -> ToolExecutionResponse:
    if EXPECTED_TOKEN:
        if credentials is None or credentials.scheme != "Bearer":
            return ToolExecutionResponse(error="Authentication required")
        if credentials.credentials != EXPECTED_TOKEN:
            return ToolExecutionResponse(error="Invalid authentication token")

    tool_name = request.tool_name
    kwargs = request.kwargs
    timeout = request.timeout

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
    if not SANDBOX_MODE:
        print(
            "WARNING: NANO_STRIX_SANDBOX_MODE is not set to true. "
            "This server should only run inside a sandbox container.",
            file=sys.stderr,
            flush=True,
        )

    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: 运行单元测试**

```bash
.venv/bin/pytest tests/test_tool_server.py -v
```

Expected: 14 个测试全部通过。

- [ ] **Step 3: Commit**

```bash
git add src/nano_strix/sandbox/tool_server.py
git commit -m "feat: rewrite tool server with FastAPI, health check, extensible scanners"
```

---

### Task 5: 构建带扫描工具的 Dockerfile.sandbox

**Files:**
- Rewrite: `Dockerfile.sandbox`

- [ ] **Step 1: 编写新的 Dockerfile**

```dockerfile
FROM python:3.12-slim

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    curl git build-essential nodejs npm \
    ca-certificates gnupg && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

# pipx for isolated tool installation
RUN pip install --no-cache-dir pipx && pipx ensurepath

# Static analysis tools
RUN pipx install semgrep && pipx install bandit

# npm-based scanners
RUN npm install -g eslint@latest && \
    npm install -g retire@latest && \
    npm install -g jshint && \
    npm install -g @ast-grep/cli@latest

# gitleaks
RUN arch=$(uname -m) && \
    case "$arch" in \
        x86_64) GITLEAKS_ARCH="x64" ;; \
        aarch64|arm64) GITLEAKS_ARCH="arm64" ;; \
        *) echo "Unsupported arch: $arch"; exit 1 ;; \
    esac && \
    TAG=$(curl -sSfL https://api.github.com/repos/gitleaks/gitleaks/releases/latest | \
          python3 -c "import sys,json; print(json.load(sys.stdin)['tag_name'])") && \
    curl -sSfL "https://github.com/gitleaks/gitleaks/releases/download/${TAG}/gitleaks_${TAG#v}_linux_${GITLEAKS_ARCH}.tar.gz" \
         -o /tmp/gitleaks.tgz && \
    tar -xzf /tmp/gitleaks.tgz -C /tmp && \
    install -m 0755 /tmp/gitleaks /usr/local/bin/gitleaks && \
    rm -f /tmp/gitleaks /tmp/gitleaks.tgz

# trufflehog
RUN curl -sSfL https://raw.githubusercontent.com/trufflesecurity/trufflehog/main/scripts/install.sh | sh -s -- -b /usr/local/bin

# Install nano-strix
COPY pyproject.toml /app/
COPY src/ /app/src/
WORKDIR /app
RUN pip install --no-cache-dir -e .

ENV NANO_STRIX_SANDBOX_MODE=true
ENV TOOL_SERVER_PORT=8080
ENV PYTHONPATH=/app
ENV PIPX_HOME=/opt/pipx
ENV PIPX_BIN_DIR=/usr/local/bin
ENV PATH="/usr/local/bin:$PATH"

RUN mkdir -p /workspace

EXPOSE 8080
CMD ["python", "-m", "nano_strix.sandbox.tool_server", "--host", "0.0.0.0", "--port", "8080"]
```

- [ ] **Step 2: 构建镜像**

```bash
docker build -f Dockerfile.sandbox -t nano-strix-sandbox:latest .
```

- [ ] **Step 3: Commit**

```bash
git add Dockerfile.sandbox
git commit -m "feat: build scanning sandbox image with semgrep, bandit, gitleaks, trufflehog, eslint"
```

---

### Task 6: 改进 DockerSandbox 容器生命周期

**Files:**
- Modify: `src/nano_strix/sandbox/docker.py`

- [ ] **Step 1: 重写 docker.py**

```python
# src/nano_strix/sandbox/docker.py
from __future__ import annotations

import asyncio
import logging
import secrets
import socket
import subprocess
import time as _time
from pathlib import Path
from typing import Any

from nano_strix.sandbox.base import ExecutionResult, Sandbox

logger = logging.getLogger(__name__)

CONTAINER_TOOL_SERVER_PORT = 8080


class DockerSandbox(Sandbox):
    def __init__(
        self,
        image: str = "nano-strix-sandbox:latest",
        network: str = "bridge",
        source_dir: Path | None = None,
        tool_server_port: int | None = None,
        auth_enabled: bool = False,
    ) -> None:
        self._image = image
        self._network = network
        self._source_dir = source_dir
        self._host_port: int = tool_server_port or 0
        self._auth_enabled = auth_enabled
        self._tool_server_token: str | None = None
        self._container: Any = None
        self._client: Any = None
        self._tool_server_url: str = ""

    @property
    def tool_server_url(self) -> str:
        return self._tool_server_url

    @property
    def auth_token(self) -> str | None:
        return self._tool_server_token

    @staticmethod
    def _find_available_port() -> int:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("", 0))
            return int(s.getsockname()[1])

    def _verify_image_available(self, max_retries: int = 3) -> None:
        import docker
        from docker.errors import ImageNotFound

        for attempt in range(max_retries):
            try:
                image = self._client.images.get(self._image)
                if image and image.id:
                    return
            except (ImageNotFound, Exception):
                if attempt == max_retries - 1:
                    raise
                _time.sleep(2 ** attempt)

    def _wait_for_tool_server(self, max_retries: int = 30) -> None:
        import json
        import urllib.request

        health_url = f"http://localhost:{self._host_port}/health"

        for attempt in range(max_retries):
            try:
                req = urllib.request.Request(health_url)
                with urllib.request.urlopen(req, timeout=5) as resp:
                    data = json.loads(resp.read())
                    if data.get("status") == "healthy":
                        logger.info(
                            "Tool server healthy on port %d (attempt %d)",
                            self._host_port, attempt + 1,
                        )
                        return
            except Exception:
                pass
            delay = min(2 ** attempt * 0.5, 5)
            _time.sleep(delay)

        raise RuntimeError(
            f"Tool server on port {self._host_port} did not become healthy "
            f"within {max_retries} attempts"
        )

    async def create(self) -> None:
        try:
            import docker
        except ImportError:
            raise ImportError("docker package required: pip install docker")

        self._client = docker.from_env()

        if self._auth_enabled:
            self._tool_server_token = secrets.token_urlsafe(32)

        if not self._host_port:
            self._host_port = self._find_available_port()

        self._verify_image_available(max_retries=3)

        volumes = {}
        if self._source_dir:
            volumes[str(self._source_dir)] = {
                "bind": "/workspace/source",
                "mode": "ro",
            }

        env_vars = {
            "NANO_STRIX_SANDBOX_MODE": "true",
            "TOOL_SERVER_PORT": str(CONTAINER_TOOL_SERVER_PORT),
            "TOOL_SERVER_TOKEN": self._tool_server_token or "",
            "PYTHONUNBUFFERED": "1",
        }

        self._container = self._client.containers.run(
            self._image,
            command=[
                "python", "-m", "nano_strix.sandbox.tool_server",
                "--host", "0.0.0.0",
                "--port", str(CONTAINER_TOOL_SERVER_PORT),
            ],
            network=self._network,
            volumes=volumes,
            ports={f"{CONTAINER_TOOL_SERVER_PORT}/tcp": self._host_port},
            environment=env_vars,
            detach=True,
            remove=True,
        )

        await asyncio.to_thread(self._wait_for_tool_server)
        self._tool_server_url = f"http://localhost:{self._host_port}"

    async def destroy(self) -> None:
        if self._container:
            container_name = self._container.name
            try:
                self._container.stop(timeout=5)
            except Exception:
                pass
            try:
                self._container.remove(force=True)
            except Exception:
                subprocess.Popen(
                    ["docker", "rm", "-f", container_name],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                )
            self._container = None
            self._host_port = 0

    async def execute(self, command: str, timeout: int = 30) -> ExecutionResult:
        import aiohttp

        start = _time.monotonic()
        try:
            timeout_obj = aiohttp.ClientTimeout(total=timeout + 5)
            headers = self._auth_headers()
            async with aiohttp.ClientSession(timeout=timeout_obj) as session:
                async with session.post(
                    f"{self._tool_server_url}/tools/terminal_execute",
                    json={"command": command, "timeout": timeout},
                    headers=headers,
                ) as resp:
                    data = await resp.json()
                    return ExecutionResult(
                        exit_code=data.get("exit_code", -1),
                        stdout=data.get("stdout", ""),
                        stderr=data.get("stderr", ""),
                        duration=_time.monotonic() - start,
                    )
        except Exception as e:
            return ExecutionResult(
                exit_code=-1,
                stdout="",
                stderr=str(e),
                duration=_time.monotonic() - start,
            )

    async def copy_in(self, local_path: str, sandbox_path: str) -> None:
        if not self._container:
            return
        import io
        import tarfile

        local = Path(local_path)
        tar_stream = io.BytesIO()
        with tarfile.open(fileobj=tar_stream, mode="w") as tar:
            if local.is_dir():
                for item in local.rglob("*"):
                    if item.is_file():
                        rel = item.relative_to(local)
                        arcname = Path(sandbox_path).name / rel
                        tar.add(str(item), arcname=str(arcname))
            else:
                tar.add(str(local), arcname=Path(sandbox_path).name)
        tar_stream.seek(0)
        self._container.put_archive(str(Path(sandbox_path).parent), tar_stream)

    async def copy_out(self, sandbox_path: str, local_path: str) -> None:
        if not self._container:
            return
        import io
        import tarfile

        bits, _ = self._container.get_archive(sandbox_path)
        tar_stream = io.BytesIO()
        for chunk in bits:
            tar_stream.write(chunk)
        tar_stream.seek(0)
        with tarfile.open(fileobj=tar_stream, mode="r") as tar:
            tar.extractall(Path(local_path).parent)

    def _auth_headers(self) -> dict[str, str]:
        if self._tool_server_token:
            return {"Authorization": f"Bearer {self._tool_server_token}"}
        return {}

    async def call_tool_server(
        self, tool_name: str, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        import aiohttp

        endpoint_map = {
            "semgrep": "/tools/scanner/semgrep",
            "bandit": "/tools/scanner/bandit",
            "gitleaks": "/tools/scanner/gitleaks",
            "trufflehog": "/tools/scanner/trufflehog",
            "eslint": "/tools/scanner/eslint",
            "retire": "/tools/scanner/retire",
            "jshint": "/tools/scanner/jshint",
            "file_read": "/tools/file_read",
            "terminal_execute": "/tools/terminal_execute",
        }
        endpoint = endpoint_map.get(tool_name, f"/tools/scanner/{tool_name}")

        try:
            timeout = aiohttp.ClientTimeout(
                total=arguments.get("timeout", 120) + 10
            )
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(
                    f"{self._tool_server_url}{endpoint}",
                    json=arguments,
                    headers=self._auth_headers(),
                ) as resp:
                    return await resp.json()
        except Exception as e:
            return {"error": str(e), "exit_code": -1}
```

- [ ] **Step 2: 运行已有 Docker 测试**

```bash
.venv/bin/pytest tests/test_docker_sandbox.py -v
```

Expected: 2 个已有测试通过。

- [ ] **Step 3: Commit**

```bash
git add src/nano_strix/sandbox/docker.py
git commit -m "feat: improve Docker sandbox lifecycle with health check, port discovery, dir copy"
```

---

### Task 7: 添加 Sandbox ContextVar 并扩展 Scanner 工具

**Files:**
- Modify: `src/nano_strix/tools/context.py`
- Modify: `src/nano_strix/tools/scanner/scanner_actions.py`
- Modify: `src/nano_strix/tools/scanner/scanner_schema.xml`

- [ ] **Step 1: 在 context.py 中添加 `current_sandbox` ContextVar**

```python
# src/nano_strix/tools/context.py — 新增 current_sandbox
from __future__ import annotations

from contextvars import ContextVar

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
```

- [ ] **Step 2: 在 scanner_actions.py 中添加静态扫描工具，支持 sandbox 路由**

```python
# src/nano_strix/tools/scanner/scanner_actions.py
# 在现有代码后面追加以下工具函数

@register_tool
async def semgrep_scan(target: str = "/workspace/source") -> dict[str, Any]:
    """Run semgrep static analysis on a target directory.

    Args:
        target: Directory or file path to scan.
    """
    from nano_strix.tools.context import get_current_sandbox

    sandbox = get_current_sandbox()
    if sandbox is not None:
        return await sandbox.call_tool_server("semgrep", {"target": target})

    # Fallback: host subprocess
    return await _run_scanner(
        "semgrep", ["--config", "auto", "--json", "--no-git-ignore", target]
    )


@register_tool
async def bandit_scan(target: str = "/workspace/source") -> dict[str, Any]:
    """Run bandit Python security scanner on a target directory.

    Args:
        target: Directory or file path to scan.
    """
    from nano_strix.tools.context import get_current_sandbox

    sandbox = get_current_sandbox()
    if sandbox is not None:
        return await sandbox.call_tool_server("bandit", {"target": target})

    return await _run_scanner("bandit", ["-r", "-f", "json", target])


@register_tool
async def gitleaks_scan(target: str = "/workspace/source") -> dict[str, Any]:
    """Run gitleaks to detect secrets and credentials in source code.

    Args:
        target: Directory to scan for secrets.
    """
    from nano_strix.tools.context import get_current_sandbox

    sandbox = get_current_sandbox()
    if sandbox is not None:
        return await sandbox.call_tool_server("gitleaks", {"target": target})

    return await _run_scanner(
        "gitleaks", ["detect", "--source", target, "--no-git", "-f", "json"]
    )


@register_tool
async def trufflehog_scan(target: str = "/workspace/source") -> dict[str, Any]:
    """Run trufflehog to find secrets and verify them.

    Args:
        target: Directory to scan for secrets.
    """
    from nano_strix.tools.context import get_current_sandbox

    sandbox = get_current_sandbox()
    if sandbox is not None:
        return await sandbox.call_tool_server("trufflehog", {"target": target})

    return await _run_scanner("trufflehog", ["filesystem", target, "--json"])


@register_tool
async def eslint_scan(target: str = "/workspace/source") -> dict[str, Any]:
    """Run ESLint static analysis on JavaScript/TypeScript files.

    Args:
        target: Directory or file path to scan.
    """
    from nano_strix.tools.context import get_current_sandbox

    sandbox = get_current_sandbox()
    if sandbox is not None:
        return await sandbox.call_tool_server("eslint", {"target": target})

    return await _run_scanner("eslint", [target, "--format", "json"])
```

同时更新 `__init__.py` 导出：

```python
# src/nano_strix/tools/scanner/__init__.py
from nano_strix.tools.scanner.scanner_actions import (
    bandit_scan,
    eslint_scan,
    gitleaks_scan,
    nikto_scan,
    nmap_scan,
    semgrep_scan,
    sqlmap_scan,
    trufflehog_scan,
)

__all__ = [
    "nmap_scan", "nikto_scan", "sqlmap_scan",
    "semgrep_scan", "bandit_scan", "gitleaks_scan",
    "trufflehog_scan", "eslint_scan",
]
```

- [ ] **Step 3: 更新 scanner_schema.xml 添加新工具定义**

```xml
<!-- src/nano_strix/tools/scanner/scanner_schema.xml — 在现有内容后面追加 -->
  <tool name="semgrep_scan">
    <description>Run semgrep static analysis on a target directory to find code vulnerabilities and bugs.</description>
    <parameters>
      <parameter name="target" type="string" required="false">
        <description>Directory or file path to scan (default: /workspace/source).</description>
      </parameter>
    </parameters>
  </tool>
  <tool name="bandit_scan">
    <description>Run bandit Python security scanner on a target directory.</description>
    <parameters>
      <parameter name="target" type="string" required="false">
        <description>Directory or file path to scan (default: /workspace/source).</description>
      </parameter>
    </parameters>
  </tool>
  <tool name="gitleaks_scan">
    <description>Run gitleaks to detect hardcoded secrets and credentials in source code.</description>
    <parameters>
      <parameter name="target" type="string" required="false">
        <description>Directory to scan for secrets (default: /workspace/source).</description>
      </parameter>
    </parameters>
  </tool>
  <tool name="trufflehog_scan">
    <description>Run trufflehog to find and verify secrets in source code.</description>
    <parameters>
      <parameter name="target" type="string" required="false">
        <description>Directory to scan for secrets (default: /workspace/source).</description>
      </parameter>
    </parameters>
  </tool>
  <tool name="eslint_scan">
    <description>Run ESLint static analysis on JavaScript/TypeScript files.</description>
    <parameters>
      <parameter name="target" type="string" required="false">
        <description>Directory or file path to scan (default: /workspace/source).</description>
      </parameter>
    </parameters>
  </tool>
```

- [ ] **Step 4: 验证工具注册成功**

```bash
.venv/bin/python -c "
from nano_strix.tools.registry import get_tool_names
names = get_tool_names()
print('Registered tools:', names)
assert 'semgrep_scan' in names
assert 'bandit_scan' in names
assert 'gitleaks_scan' in names
print('All new scanner tools registered OK')
"
```

- [ ] **Step 5: Commit**

```bash
git add src/nano_strix/tools/context.py \
        src/nano_strix/tools/scanner/scanner_actions.py \
        src/nano_strix/tools/scanner/scanner_schema.xml \
        src/nano_strix/tools/scanner/__init__.py
git commit -m "feat: add static analysis tools with sandbox routing via context var"
```

---

### Task 8: 新增 DockerSandbox 集成测试

**Files:**
- Modify: `tests/test_docker_sandbox.py`

- [ ] **Step 1: 添加 health check 测试**

在 `TestDockerSandbox` 类中添加：

```python
    async def test_health_endpoint(self):
        """Verify the tool server /health endpoint is reachable."""
        import aiohttp

        sb = DockerSandbox(image="nano-strix-sandbox:latest")
        await sb.create()
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{sb._tool_server_url}/health"
                ) as resp:
                    assert resp.status == 200
                    data = await resp.json()
                    assert data["status"] == "healthy"
        finally:
            await sb.destroy()
```

- [ ] **Step 2: 运行全部 Docker 测试**

```bash
.venv/bin/pytest tests/test_docker_sandbox.py -v
```

Expected: 3 个测试全部通过。

- [ ] **Step 3: Commit**

```bash
git add tests/test_docker_sandbox.py
git commit -m "test: add health check test for DockerSandbox tool server"
```

---

### Task 9: 端到端验证

- [ ] **Step 1: 重建沙箱镜像**

```bash
docker build -f Dockerfile.sandbox -t nano-strix-sandbox:latest .
```

- [ ] **Step 2: 运行全部沙箱相关测试**

```bash
.venv/bin/pytest tests/test_tool_server.py tests/test_docker_sandbox.py -v
```

- [ ] **Step 3: 运行全量测试检查回归**

```bash
.venv/bin/pytest -v
```

- [ ] **Step 4: 手动验证 sandbox 路由链路**

```bash
.venv/bin/python -c "
import asyncio
from nano_strix.sandbox.docker import DockerSandbox
from nano_strix.tools.context import set_current_sandbox
from nano_strix.tools.scanner.scanner_actions import semgrep_scan

async def test():
    sb = DockerSandbox(image='nano-strix-sandbox:latest')
    await sb.create()
    set_current_sandbox(sb)
    try:
        # 通过 sandbox 路由执行 semgrep
        result = await semgrep_scan('/workspace/source')
        print('semgrep exit_code:', result.get('exit_code'))
        print('semgrep stdout:', result.get('stdout', '')[:200])
    finally:
        await sb.destroy()

asyncio.run(test())
"
```

---

## 工具调用链路总结

```
ScanAgent LLM
  → _process_iteration()
    → get_tool_by_name("semgrep_scan")
    → execute_tool_with_validation("semgrep_scan", {target: "/workspace/source"})
      → semgrep_scan(target)
        → get_current_sandbox() → DockerSandbox instance
        → sandbox.call_tool_server("semgrep", {target: ...})
          → HTTP POST → tool_server /tools/scanner/semgrep
            → subprocess.run(["semgrep", ...])  # inside container
          ← JSON response {exit_code, stdout, stderr}
      ← {exit_code: 0/1, stdout: "...", stderr: "..."}
  → LLM sees tool result
```

## Verification Summary

1. `pytest tests/test_tool_server.py -v` — 14 个单元测试通过（无需 Docker）
2. `pytest tests/test_docker_sandbox.py -v` — 3 个集成测试通过（需要 Docker）
3. `pytest -v` — 全量测试套件，无回归
4. `docker build -f Dockerfile.sandbox -t nano-strix-sandbox:latest .` — 镜像构建成功
5. 新增 5 个 `@register_tool` 工具 (`semgrep_scan`, `bandit_scan`, `gitleaks_scan`, `trufflehog_scan`, `eslint_scan`) 支持 sandbox 路由
