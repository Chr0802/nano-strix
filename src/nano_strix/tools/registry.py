from __future__ import annotations

import xml.etree.ElementTree as ET
from collections.abc import Callable
from functools import wraps
from pathlib import Path
from typing import Any

tools: list[dict[str, Any]] = []
_tools_by_name: dict[str, Callable[..., Any]] = {}
_tool_param_schemas: dict[str, dict[str, Any]] = {}


def register_tool(func: Callable[..., Any]) -> Callable[..., Any]:
    name = func.__name__
    module_parts = func.__module__.split(".")
    module = module_parts[-2] if len(module_parts) >= 2 else module_parts[-1]

    func_dict = {
        "name": name,
        "function": func,
        "module": module,
    }

    schema_path = _find_schema_path(func, module)
    if schema_path and schema_path.exists():
        _tool_param_schemas[name] = _parse_param_schema(schema_path)

    tools.append(func_dict)
    _tools_by_name[name] = func

    @wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        return func(*args, **kwargs)

    return wrapper


def _find_schema_path(func: Callable, module: str) -> Path | None:
    import inspect

    original = inspect.unwrap(func)
    func_file = Path(original.__code__.co_filename)
    schema_name = f"{module}_schema.xml"
    schema_path = func_file.parent / schema_name
    if schema_path.exists():
        return schema_path
    return None


def _parse_param_schema(schema_path: Path) -> dict[str, Any]:
    tree = ET.parse(schema_path)
    root = tree.getroot()

    params: list[dict[str, Any]] = []
    required: list[str] = []

    for tool_elem in root.findall(".//tool"):
        for param_elem in tool_elem.findall(".//parameter"):
            name = param_elem.get("name", "")
            ptype = param_elem.get("type", "string")
            is_required = param_elem.get("required", "false").lower() == "true"
            desc_elem = param_elem.find("description")
            description = desc_elem.text if desc_elem is not None else ""

            params.append(
                {
                    "name": name,
                    "type": ptype,
                    "description": description,
                    "required": is_required,
                }
            )
            if is_required:
                required.append(name)

    properties = {}
    for p in params:
        json_type = _xml_type_to_json(p["type"])
        prop: dict[str, Any] = {"type": json_type, "description": p["description"]}
        properties[p["name"]] = prop

    return {
        "type": "object",
        "properties": properties,
        "required": required,
    }


def _xml_type_to_json(xml_type: str) -> str:
    mapping = {
        "string": "string",
        "number": "number",
        "boolean": "boolean",
        "integer": "integer",
    }
    return mapping.get(xml_type, "string")


def get_tool_by_name(name: str) -> Callable[..., Any]:
    if name not in _tools_by_name:
        registered = list(_tools_by_name.keys())
        raise KeyError(f"Unknown tool: {name}. Registered: {registered}")
    return _tools_by_name[name]


def get_tool_names() -> list[str]:
    return list(_tools_by_name.keys())


def get_tool_param_schema(name: str) -> dict[str, Any]:
    return _tool_param_schemas.get(name, {})


def get_tools_prompt() -> str:
    modules: dict[str, list[dict[str, Any]]] = {}
    for func_dict in tools:
        module = func_dict["module"]
        if module not in modules:
            modules[module] = []
        modules[module].append(func_dict)

    def xml_close(tag: str) -> str:
        return chr(60) + "/" + tag + chr(62)

    parts = ["<tools>"]
    for module, tool_dicts in modules.items():
        parts.append("  " + f"<{module}_tools>")
        for td in tool_dicts:
            name = td["name"]
            schema = _tool_param_schemas.get(name, {})
            parts.append('    <tool name="' + name + '">')
            props = schema.get("properties", {})
            required = schema.get("required", [])
            for param_name, param_info in props.items():
                req = "true" if param_name in required else "false"
                ptype = param_info["type"]
                desc = param_info.get("description", "")
                param_line = (
                    '      <parameter name="'
                    + param_name
                    + '"'
                    + ' type="'
                    + ptype
                    + '" required="'
                    + req
                    + '">'
                    + desc
                    + xml_close("parameter")
                )
                parts.append(param_line)
            parts.append("    " + xml_close("tool"))
        parts.append("  " + xml_close(module + "_tools"))
    parts.append(xml_close("tools"))
    return "\n".join(parts)


def clear_registry() -> None:
    tools.clear()
    _tools_by_name.clear()
    _tool_param_schemas.clear()
