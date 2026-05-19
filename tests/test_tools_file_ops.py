from __future__ import annotations

from pathlib import Path

from nano_strix.tools.file_ops.file_ops_actions import (
    directory_list,
    file_read,
    file_search,
    file_write,
)


def test_file_read(tmp_path: Path):
    f = tmp_path / "test.txt"
    f.write_text("line1\nline2\nline3")

    result = file_read(str(f))
    assert result["path"] == str(f)
    assert "line1" in result["content"]
    assert result["lines"] == 3


def test_file_read_not_found():
    result = file_read("/nonexistent/file.txt")
    assert "error" in result


def test_file_read_max_lines(tmp_path: Path):
    f = tmp_path / "many.txt"
    f.write_text("\n".join(f"line{i}" for i in range(100)))

    result = file_read(str(f), max_lines=10)
    assert result["truncated"] is True
    assert result["shown_lines"] == 10


def test_file_write(tmp_path: Path):
    f = tmp_path / "out.txt"
    result = file_write(str(f), "hello world")
    assert result["path"] == str(f)
    assert result["bytes_written"] > 0
    assert f.read_text() == "hello world"


def test_file_write_creates_dirs(tmp_path: Path):
    f = tmp_path / "sub" / "dir" / "out.txt"
    file_write(str(f), "nested")
    assert f.read_text() == "nested"


def test_directory_list(tmp_path: Path):
    (tmp_path / "a.txt").write_text("a")
    (tmp_path / "b.txt").write_text("b")
    (tmp_path / "sub").mkdir()

    result = directory_list(str(tmp_path))
    assert result["count"] == 3
    names = [e["name"] for e in result["entries"]]
    assert "a.txt" in names
    assert "sub" in names


def test_directory_list_recursive(tmp_path: Path):
    sub = tmp_path / "sub"
    sub.mkdir()
    (tmp_path / "a.txt").write_text("a")
    (sub / "b.txt").write_text("b")

    result = directory_list(str(tmp_path), recursive=True)
    names = [e["name"] for e in result["entries"]]
    assert "a.txt" in names
    assert "b.txt" in names


def test_directory_list_not_found():
    result = directory_list("/nonexistent/dir")
    assert "error" in result


def test_file_search(tmp_path: Path):
    (tmp_path / "test.py").write_text("x = 1")
    (tmp_path / "test.txt").write_text("hello")
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "nested.py").write_text("y = 2")

    result = file_search(str(tmp_path), "*.py")
    assert result["count"] == 2
    paths = result["matches"]
    assert any("test.py" in p for p in paths)
    assert any("nested.py" in p for p in paths)
