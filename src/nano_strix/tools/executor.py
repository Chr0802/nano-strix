from __future__ import annotations

import asyncio
import logging
from typing import Any

from nano_strix.tools.argument_parser import convert_arguments
from nano_strix.tools.registry import (
    get_tool_by_name,
    get_tool_names,
    get_tool_param_schema,
)

logger = logging.getLogger(__name__)

MAX_RESULT_CHARS = 10000
TRUNCATE_KEEP = 4000


async def execute_tool(tool_name: str, **kwargs: Any) -> dict[str, Any]:
    tool_func = get_tool_by_name(tool_name)
    converted = convert_arguments(tool_func, kwargs)

    result = tool_func(**converted)
    if asyncio.iscoroutine(result) or asyncio.isfuture(result):
        result = await result

    return result


def validate_tool_availability(tool_name: str) -> None:
    if tool_name not in get_tool_names():
        raise KeyError(f"Unknown tool: {tool_name}. Available: {get_tool_names()}")


def validate_tool_arguments(tool_name: str, kwargs: dict[str, Any]) -> None:
    schema = get_tool_param_schema(tool_name)
    required = schema.get("required", [])
    properties = schema.get("properties", {})

    for param in required:
        if param not in kwargs:
            raise ValueError(f"Missing required parameter: {param}")

    for key in kwargs:
        if key not in properties:
            logger.warning("Unknown parameter '%s' for tool '%s'", key, tool_name)


async def execute_tool_with_validation(
    tool_name: str, kwargs: dict[str, Any]
) -> dict[str, Any]:
    validate_tool_availability(tool_name)
    validate_tool_arguments(tool_name, kwargs)

    try:
        return await execute_tool(tool_name, **kwargs)
    except Exception as e:
        error_msg = str(e)[:500]
        logger.error("Tool %s failed: %s", tool_name, error_msg)
        return {"error": error_msg}


def format_tool_result(tool_name: str, result: dict[str, Any]) -> str:
    if "error" in result:
        return f"[{tool_name}] Error: {result['error']}"

    parts = []
    for key, value in result.items():
        if isinstance(value, str):
            parts.append(f"{key}: {value}")
        else:
            parts.append(f"{key}: {value}")

    output = "\n".join(parts)

    if len(output) > MAX_RESULT_CHARS:
        head = output[:TRUNCATE_KEEP]
        tail = output[-TRUNCATE_KEEP:]
        truncated_chars = len(output) - 2 * TRUNCATE_KEEP
        output = f"{head}\n\n... [truncated {truncated_chars} chars] ...\n\n{tail}"

    return output
