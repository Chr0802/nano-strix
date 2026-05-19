from __future__ import annotations

import pytest

from nano_strix.tools.terminal.terminal_actions import terminal_execute


@pytest.mark.asyncio
async def test_terminal_execute_echo():
    result = await terminal_execute("echo hello")
    assert result["exit_code"] == 0
    assert "hello" in result["stdout"]


@pytest.mark.asyncio
async def test_terminal_execute_failing_command():
    result = await terminal_execute("false")
    assert result["exit_code"] != 0


@pytest.mark.asyncio
async def test_terminal_execute_stderr():
    result = await terminal_execute("echo error >&2")
    assert result["exit_code"] == 0
    assert "error" in result["stderr"]


@pytest.mark.asyncio
async def test_terminal_execute_timeout():
    result = await terminal_execute("sleep 60", timeout=1)
    assert "error" in result
    assert "timed out" in result["error"]
