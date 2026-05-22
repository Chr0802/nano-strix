import pytest
from nano_strix.sandbox.docker import DockerSandbox


def _docker_available() -> tuple[bool, str]:
    """Check if Docker SDK and sandbox image are available."""
    try:
        import docker

        client = docker.from_env()
        client.ping()
    except ImportError:
        return False, "Python docker SDK not installed (pip install docker)"
    except Exception as e:
        return False, f"Docker daemon not reachable: {e}"

    try:
        client.images.get("nano-strix-sandbox:latest")
    except Exception:
        return (
            False,
            "Sandbox image 'nano-strix-sandbox:latest' not found. "
            "Build it with: docker build -f Dockerfile.sandbox -t nano-strix-sandbox:latest .",
        )

    return True, ""


_available, _skip_reason = _docker_available()


@pytest.mark.skipif(not _available, reason=_skip_reason)
class TestDockerSandbox:
    async def test_create_and_destroy(self):
        sb = DockerSandbox(image="nano-strix-sandbox:latest")
        await sb.create()
        assert sb._container is not None
        status = sb._container.status
        assert status == "running"
        await sb.destroy()

    async def test_execute_command(self):
        sb = DockerSandbox(image="nano-strix-sandbox:latest")
        await sb.create()
        result = await sb.execute("echo hello", timeout=10)
        assert result.exit_code == 0
        assert "hello" in result.stdout
        await sb.destroy()

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
