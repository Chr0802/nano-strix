from __future__ import annotations

import asyncio
from typing import Any

from nano_strix.tools.registry import register_tool


async def _run_local(command: str, timeout: int = 30) -> dict[str, Any]:
    """Execute a shell command on the host machine."""
    try:
        process = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(), timeout=timeout
            )
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
            return {
                "error": f"Command timed out after {timeout}s",
                "command": command,
            }

        return {
            "exit_code": process.returncode,
            "stdout": stdout.decode(errors="replace"),
            "stderr": stderr.decode(errors="replace"),
            "command": command,
        }
    except Exception as e:
        return {"error": str(e), "command": command}


@register_tool
async def terminal_execute(command: str, timeout: int = 30) -> dict[str, Any]:
    """Execute a shell command, routing through Docker sandbox if available."""
    from nano_strix.tools.context import get_current_sandbox

    sandbox = get_current_sandbox()
    if sandbox is not None:
        return await sandbox.call_tool_server(
            "terminal_execute", {"command": command, "timeout": timeout}
        )

    return await _run_local(command, timeout)
