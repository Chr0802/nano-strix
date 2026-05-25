from __future__ import annotations

from pathlib import Path

import pytest

from nano_strix.tools.context import set_current_workspace_root, current_workspace_root
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


# ---- Path restriction tests ----


class TestPathRestriction:
    """Verify file ops reject paths outside the workspace root."""

    @pytest.fixture(autouse=True)
    def _setup_workspace(self, tmp_path: Path):
        """Set workspace root to tmp_path for each test."""
        root = str(tmp_path.resolve())
        set_current_workspace_root(root)

    @pytest.fixture(autouse=True)
    def _cleanup_workspace(self):
        """Reset workspace root after each test."""
        yield
        current_workspace_root.set(None)

    def test_file_read_within_workspace(self, tmp_path: Path):
        f = tmp_path / "allowed.py"
        f.write_text("x = 1")
        result = file_read(str(f))
        assert "error" not in result
        assert result["content"] == "x = 1"

    def test_file_read_outside_workspace(self, tmp_path: Path):
        outside = tmp_path.parent / "outside.txt"
        outside.write_text("secret")
        result = file_read(str(outside))
        assert "Access denied" in result["error"]

    def test_file_read_path_traversal(self, tmp_path: Path):
        result = file_read(str(tmp_path / "../../../etc/passwd"))
        assert "Access denied" in result["error"]

    def test_file_read_relative_path_resolved(self, tmp_path: Path):
        f = tmp_path / "rel.py"
        f.write_text("a = 1")
        result = file_read("rel.py")
        assert "error" not in result
        assert result["content"] == "a = 1"

    def test_directory_list_within_workspace(self, tmp_path: Path):
        result = directory_list(str(tmp_path))
        assert "error" not in result

    def test_directory_list_outside_workspace(self, tmp_path: Path):
        result = directory_list(str(tmp_path.parent))
        assert "Access denied" in result["error"]

    def test_file_search_within_workspace(self, tmp_path: Path):
        (tmp_path / "a.py").write_text("1")
        result = file_search(str(tmp_path), "*.py")
        assert "error" not in result
        assert result["count"] == 1

    def test_file_search_outside_workspace(self, tmp_path: Path):
        result = file_search(str(tmp_path.parent), "*")
        assert "Access denied" in result["error"]

    def test_file_write_within_workspace(self, tmp_path: Path):
        result = file_write(str(tmp_path / "report.json"), "{}")
        assert "error" not in result
        assert (tmp_path / "report.json").read_text() == "{}"

    def test_file_write_outside_workspace(self, tmp_path: Path):
        result = file_write(str(tmp_path.parent / "malware.sh"), "echo pwn")
        assert "Access denied" in result["error"]
