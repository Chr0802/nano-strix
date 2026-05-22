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
        self._container.reload()
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
