from __future__ import annotations

from nano_strix.tools.registry import (
    clear_registry,
    get_tool_by_name,
    get_tool_names,
    get_tool_param_schema,
    get_tools_prompt,
    register_tool,
    tools,
)


def setup_function():
    clear_registry()


def test_register_tool():
    @register_tool
    def my_tool(x: str) -> dict:
        return {"result": x}

    assert "my_tool" in get_tool_names()
    assert callable(get_tool_by_name("my_tool"))


def test_get_tool_by_name():
    @register_tool
    def another_tool() -> dict:
        return {}

    tool = get_tool_by_name("another_tool")
    assert callable(tool)


def test_get_unknown_tool():
    try:
        get_tool_by_name("nonexistent")
        assert False, "Should have raised KeyError"
    except KeyError as e:
        assert "nonexistent" in str(e)


def test_get_tool_names():
    @register_tool
    def tool_a() -> dict:
        return {}

    @register_tool
    def tool_b() -> dict:
        return {}

    names = get_tool_names()
    assert "tool_a" in names
    assert "tool_b" in names


def test_clear_registry():
    @register_tool
    def temp_tool() -> dict:
        return {}

    assert "temp_tool" in get_tool_names()
    clear_registry()
    assert "temp_tool" not in get_tool_names()
    assert len(tools) == 0


def test_get_tools_prompt():
    @register_tool
    def sample_tool() -> dict:
        return {}

    prompt = get_tools_prompt()
    assert "<tools>" in prompt
    assert "sample_tool" in prompt


def test_get_tool_param_schema_empty():
    @register_tool
    def no_schema_tool() -> dict:
        return {}

    schema = get_tool_param_schema("no_schema_tool")
    assert schema == {}
