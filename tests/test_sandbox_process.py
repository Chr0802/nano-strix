from pathlib import Path

import pytest

from nano_strix.sandbox.base import SandboxConfig
from nano_strix.sandbox.process import ProcessSandbox


@pytest.fixture
def sandbox(tmp_path: Path):
    cfg = SandboxConfig(timeout=30)
    return ProcessSandbox(cfg, workspace=tmp_path)


@pytest.mark.asyncio
async def test_process_execute_echo(sandbox):
    result = await sandbox.execute("echo hello")
    assert result.exit_code == 0
    assert "hello" in result.stdout


@pytest.mark.asyncio
async def test_process_execute_failing_command(sandbox):
    result = await sandbox.execute("false")
    assert result.exit_code == 1


@pytest.mark.asyncio
async def test_process_copy_in_out(sandbox, tmp_path: Path):
    src = tmp_path / "input.txt"
    src.write_text("test content")

    dest = sandbox.workspace / "copied.txt"
    await sandbox.copy_in(str(src), str(dest))
    assert dest.exists()
    assert dest.read_text() == "test content"

    out = tmp_path / "output.txt"
    await sandbox.copy_out(str(dest), str(out))
    assert out.read_text() == "test content"
