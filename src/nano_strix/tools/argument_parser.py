from __future__ import annotations

import inspect
import json
from collections.abc import Callable
from typing import Any, get_args, get_origin, get_type_hints


def convert_arguments(
    func: Callable[..., Any], kwargs: dict[str, Any]
) -> dict[str, Any]:
    sig = inspect.signature(func)
    try:
        hints = get_type_hints(func)
    except Exception:
        hints = {}

    converted = {}

    for param_name, param in sig.parameters.items():
        if param_name not in kwargs:
            continue

        value = kwargs[param_name]
        annotation = hints.get(param_name, param.annotation)

        if annotation is inspect.Parameter.empty or not isinstance(value, str):
            converted[param_name] = value
            continue

        converted[param_name] = _convert_string(value, annotation)

    for key in kwargs:
        if key not in converted:
            converted[key] = kwargs[key]

    return converted


def _convert_string(value: str, annotation: type) -> Any:
    origin = get_origin(annotation)

    if annotation is int:
        return int(value)
    if annotation is float:
        return float(value)
    if annotation is bool:
        return value.lower() in ("true", "1", "yes")
    if annotation is str:
        return value

    if annotation is list or origin is list:
        try:
            return json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return [value]

    if annotation is dict or origin is dict:
        try:
            return json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return {"value": value}

    if origin is type(None) or (origin is type and type(None) in get_args(annotation)):
        return value

    args = get_args(annotation)
    if args:
        for arg in args:
            if arg is type(None):
                continue
            try:
                return _convert_string(value, arg)
            except (ValueError, TypeError):
                continue

    return value
