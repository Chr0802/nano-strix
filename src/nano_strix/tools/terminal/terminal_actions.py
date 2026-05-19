from __future__ import annotations

import asyncio
from typing import Any

from nano_strix.tools.registry import register_tool


@register_tool
async def terminal_execute(command: str, timeout: int = 30) -> dict[str, Any]:
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
