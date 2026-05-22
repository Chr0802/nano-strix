import pytest
from nano_strix.sandbox.docker import DockerSandbox


def _docker_available():
    try:
        import docker

        client = docker.from_env()
        client.ping()
        return True
    except Exception:
        return False


@pytest.mark.skipif(not _docker_available(), reason="Docker not available")
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
