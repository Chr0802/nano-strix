from __future__ import annotations

import asyncio
import shutil
from typing import Any

from nano_strix.tools.registry import register_tool


async def _run_scanner(
    name: str, args: list[str], timeout: int = 300
) -> dict[str, Any]:
    binary = shutil.which(name)
    if not binary:
        return {"error": f"{name} not found in PATH"}

    try:
        process = await asyncio.create_subprocess_exec(
            binary,
            *args,
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
                "error": f"{name} timed out after {timeout}s",
                "command": f"{binary} {' '.join(args)}",
            }

        return {
            "exit_code": process.returncode,
            "stdout": stdout.decode(errors="replace"),
            "stderr": stderr.decode(errors="replace"),
            "command": f"{binary} {' '.join(args)}",
        }
    except Exception as e:
        return {"error": str(e)}


@register_tool
async def nmap_scan(target: str, ports: str = "", flags: str = "") -> dict[str, Any]:
    args = []
    if ports:
        args.extend(["-p", ports])
    if flags:
        args.extend(flags.split())
    args.append(target)
    return await _run_scanner("nmap", args)


@register_tool
async def nikto_scan(target: str, flags: str = "") -> dict[str, Any]:
    args = ["-h", target]
    if flags:
        args.extend(flags.split())
    return await _run_scanner("nikto", args, timeout=600)


@register_tool
async def sqlmap_scan(target: str, flags: str = "") -> dict[str, Any]:
    args = ["-u", target, "--batch"]
    if flags:
        args.extend(flags.split())
    return await _run_scanner("sqlmap", args, timeout=600)


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
