from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from nano_strix.sandbox.base import ExecutionResult, Sandbox

logger = logging.getLogger(__name__)


class DockerSandbox(Sandbox):
    def __init__(
        self,
        image: str = "nano-strix-sandbox:latest",
        network: str = "none",
        source_dir: Path | None = None,
        tool_server_port: int = 8080,
    ) -> None:
        self._image = image
        self._network = network
        self._source_dir = source_dir
        self._tool_server_port = tool_server_port
        self._container: Any = None
        self._client: Any = None
        self._tool_server_url: str = ""

    async def create(self) -> None:
        try:
            import docker
        except ImportError:
            raise ImportError("docker package required: pip install docker")

        self._client = docker.from_env()
        volumes = {}
        if self._source_dir:
            volumes[str(self._source_dir)] = {
                "bind": "/workspace/source",
                "mode": "ro",
            }

        self._container = self._client.containers.run(
            self._image,
            command=["python", "-m", "nano_strix.sandbox.tool_server"],
            network=self._network,
            volumes=volumes,
            ports={"8080/tcp": self._tool_server_port},
            detach=True,
            remove=True,
        )

        import time

        time.sleep(1)  # Wait for tool server to start
        self._tool_server_url = f"http://localhost:{self._tool_server_port}"

    async def destroy(self) -> None:
        if self._container:
            try:
                self._container.stop(timeout=5)
            except Exception:
                pass
            self._container = None

    async def execute(self, command: str, timeout: int = 30) -> ExecutionResult:
        """Execute a command in the sandbox via tool server API."""
        import time

        import aiohttp

        start = time.monotonic()
        try:
            timeout_obj = aiohttp.ClientTimeout(total=timeout + 5)
            async with aiohttp.ClientSession(timeout=timeout_obj) as session:
                async with session.post(
                    f"{self._tool_server_url}/tools/terminal_execute",
                    json={"command": command, "timeout": timeout},
                ) as resp:
                    data = await resp.json()
                    return ExecutionResult(
                        exit_code=data.get("exit_code", -1),
                        stdout=data.get("stdout", ""),
                        stderr=data.get("stderr", ""),
                        duration=time.monotonic() - start,
                    )
        except Exception as e:
            return ExecutionResult(
                exit_code=-1,
                stdout="",
                stderr=str(e),
                duration=time.monotonic() - start,
            )

    async def copy_in(self, local_path: str, sandbox_path: str) -> None:
        if self._container:
            import io
            import tarfile

            tar_stream = io.BytesIO()
            with tarfile.open(fileobj=tar_stream, mode="w") as tar:
                tar.add(local_path, arcname=Path(sandbox_path).name)
            tar_stream.seek(0)
            self._container.put_archive(str(Path(sandbox_path).parent), tar_stream)

    async def copy_out(self, sandbox_path: str, local_path: str) -> None:
        if self._container:
            bits, _ = self._container.get_archive(sandbox_path)
            import io
            import tarfile

            tar_stream = io.BytesIO()
            for chunk in bits:
                tar_stream.write(chunk)
            tar_stream.seek(0)
            with tarfile.open(fileobj=tar_stream, mode="r") as tar:
                tar.extractall(Path(local_path).parent)

    async def call_tool_server(
        self, tool_name: str, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        """Call a specific tool on the sandbox tool server."""
        import aiohttp

        endpoint_map = {
            "semgrep": "/tools/scanner/semgrep",
            "bandit": "/tools/scanner/bandit",
            "file_read": "/tools/file_read",
            "terminal_execute": "/tools/terminal_execute",
        }
        endpoint = endpoint_map.get(tool_name, f"/tools/{tool_name}")

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self._tool_server_url}{endpoint}",
                    json=arguments,
                    timeout=aiohttp.ClientTimeout(total=120),
                ) as resp:
                    return await resp.json()
        except Exception as e:
            return {"error": str(e)}
