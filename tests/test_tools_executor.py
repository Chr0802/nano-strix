from __future__ import annotations

import pytest

from nano_strix.tools.executor import (
    execute_tool,
    execute_tool_with_validation,
    format_tool_result,
    validate_tool_arguments,
    validate_tool_availability,
)
from nano_strix.tools.registry import clear_registry, register_tool


def setup_function():
    clear_registry()


@pytest.mark.asyncio
async def test_execute_tool():
    @register_tool
    def add(a: int, b: int) -> dict:
        return {"result": a + b}

    result = await execute_tool("add", a="3", b="4")
    assert result == {"result": 7}


@pytest.mark.asyncio
async def test_execute_async_tool():
    @register_tool
    async def async_add(a: int, b: int) -> dict:
        return {"result": a + b}

    result = await execute_tool("async_add", a="3", b="4")
    assert result == {"result": 7}


def test_validate_availability():
    @register_tool
    def existing() -> dict:
        return {}

    validate_tool_availability("existing")

    with pytest.raises(KeyError):
        validate_tool_availability("nonexistent")


def test_validate_arguments():
    import importlib

    from nano_strix.tools.scanner import scanner_actions

    importlib.reload(scanner_actions)

    validate_tool_arguments("nmap_scan", {"target": "127.0.0.1"})

    with pytest.raises(ValueError, match="Missing required"):
        validate_tool_arguments("nmap_scan", {"ports": "80"})


@pytest.mark.asyncio
async def test_execute_with_validation():
    @register_tool
    def greet(name: str) -> dict:
        return {"greeting": f"Hello {name}"}

    result = await execute_tool_with_validation("greet", {"name": "World"})
    assert result == {"greeting": "Hello World"}


@pytest.mark.asyncio
async def test_execute_with_validation_error():
    @register_tool
    def failing() -> dict:
        raise RuntimeError("boom")

    result = await execute_tool_with_validation("failing", {})
    assert "error" in result
    assert "boom" in result["error"]


def test_format_tool_result_success():
    result = format_tool_result("test", {"key": "value", "count": 42})
    assert "key: value" in result
    assert "count: 42" in result


def test_format_tool_result_error():
    result = format_tool_result("test", {"error": "something went wrong"})
    assert "[test] Error: something went wrong" in result


def test_format_tool_result_truncation():
    long_value = "x" * 15000
    result = format_tool_result("test", {"data": long_value})
    assert "truncated" in result
    assert len(result) < 15000
