from __future__ import annotations

from pathlib import Path
from typing import Any

from nano_strix.tools.context import (
    get_current_workspace_root,
    resolve_and_validate_path,
)
from nano_strix.tools.registry import register_tool


def _safe_path(path: str) -> Path:
    """Resolve *path* safely within the workspace root if one is configured.

    When no workspace root is set (e.g. in tests or CLI tools), the raw
    path is used as-is for backward compatibility.
    """
    if get_current_workspace_root() is not None:
        return resolve_and_validate_path(path)
    return Path(path)


@register_tool
def file_read(path: str, max_lines: int = 1000) -> dict[str, Any]:
    try:
        p = _safe_path(path)
        if not p.exists():
            return {"error": f"File not found: {path}"}
        if not p.is_file():
            return {"error": f"Not a file: {path}"}

        lines = p.read_text(errors="replace").splitlines()
        truncated = len(lines) > max_lines
        content = "\n".join(lines[:max_lines])

        result = {"path": str(p), "content": content, "lines": len(lines)}
        if truncated:
            result["truncated"] = True
            result["shown_lines"] = max_lines
        return result
    except PermissionError as e:
        return {"error": str(e), "path": path}
    except Exception as e:
        return {"error": str(e), "path": path}


@register_tool
def file_write(path: str, content: str) -> dict[str, Any]:
    try:
        p = _safe_path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
        return {"path": str(p), "bytes_written": len(content.encode())}
    except PermissionError as e:
        return {"error": str(e), "path": path}
    except Exception as e:
        return {"error": str(e), "path": path}


@register_tool
def directory_list(path: str, recursive: bool = False) -> dict[str, Any]:
    try:
        p = _safe_path(path)
        if not p.exists():
            return {"error": f"Path not found: {path}"}
        if not p.is_dir():
            return {"error": f"Not a directory: {path}"}

        entries = []
        if recursive:
            for item in sorted(p.rglob("*")):
                entries.append(
                    {
                        "name": item.name,
                        "path": str(item),
                        "type": "dir" if item.is_dir() else "file",
                    }
                )
        else:
            for item in sorted(p.iterdir()):
                entries.append(
                    {
                        "name": item.name,
                        "path": str(item),
                        "type": "dir" if item.is_dir() else "file",
                    }
                )

        return {"path": str(p), "entries": entries, "count": len(entries)}
    except PermissionError as e:
        return {"error": str(e), "path": path}
    except Exception as e:
        return {"error": str(e), "path": path}


@register_tool
def file_search(path: str, pattern: str) -> dict[str, Any]:
    try:
        p = _safe_path(path)
        if not p.exists():
            return {"error": f"Path not found: {path}"}

        matches = [str(m) for m in sorted(p.rglob(pattern))]
        return {
            "path": str(p),
            "pattern": pattern,
            "matches": matches,
            "count": len(matches),
        }
    except PermissionError as e:
        return {"error": str(e), "path": path, "pattern": pattern}
    except Exception as e:
        return {"error": str(e), "path": path, "pattern": pattern}
