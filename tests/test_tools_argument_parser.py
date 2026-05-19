from __future__ import annotations

from nano_strix.tools.argument_parser import convert_arguments


def test_convert_int():
    def func(count: int) -> None:
        pass

    result = convert_arguments(func, {"count": "42"})
    assert result["count"] == 42


def test_convert_float():
    def func(rate: float) -> None:
        pass

    result = convert_arguments(func, {"rate": "3.14"})
    assert result["rate"] == 3.14


def test_convert_bool_true():
    def func(flag: bool) -> None:
        pass

    for val in ["true", "1", "yes", "True", "YES"]:
        result = convert_arguments(func, {"flag": val})
        assert result["flag"] is True


def test_convert_bool_false():
    def func(flag: bool) -> None:
        pass

    for val in ["false", "0", "no", "False", "NO"]:
        result = convert_arguments(func, {"flag": val})
        assert result["flag"] is False


def test_convert_str():
    def func(name: str) -> None:
        pass

    result = convert_arguments(func, {"name": "hello"})
    assert result["name"] == "hello"


def test_convert_list():
    def func(items: list) -> None:
        pass

    result = convert_arguments(func, {"items": "[1, 2, 3]"})
    assert result["items"] == [1, 2, 3]


def test_convert_dict():
    def func(config: dict) -> None:
        pass

    result = convert_arguments(func, {"config": '{"key": "value"}'})
    assert result["config"] == {"key": "value"}


def test_convert_passthrough_non_string():
    def func(count: int) -> None:
        pass

    result = convert_arguments(func, {"count": 42})
    assert result["count"] == 42


def test_convert_no_annotation():
    def func(value) -> None:
        pass

    result = convert_arguments(func, {"value": "test"})
    assert result["value"] == "test"


def test_convert_extra_kwargs():
    def func(name: str) -> None:
        pass

    result = convert_arguments(func, {"name": "test", "extra": "value"})
    assert result["name"] == "test"
    assert result["extra"] == "value"
