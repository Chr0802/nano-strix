from __future__ import annotations

import importlib

import pytest

from nano_strix.tools.registry import get_tool_param_schema
from nano_strix.tools.scanner import scanner_actions


def setup_function():
    importlib.reload(scanner_actions)


def test_nmap_scan_schema():
    schema = get_tool_param_schema("nmap_scan")
    assert "target" in schema.get("properties", {})
    assert "target" in schema.get("required", [])


def test_nikto_scan_schema():
    schema = get_tool_param_schema("nikto_scan")
    assert "target" in schema.get("properties", {})
    assert "target" in schema.get("required", [])


def test_sqlmap_scan_schema():
    schema = get_tool_param_schema("sqlmap_scan")
    assert "target" in schema.get("properties", {})
    assert "target" in schema.get("required", [])


@pytest.mark.asyncio
async def test_nmap_not_found(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda x: None)
    result = await scanner_actions.nmap_scan("127.0.0.1")
    assert "error" in result
    assert "not found" in result["error"]


@pytest.mark.asyncio
async def test_nikto_not_found(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda x: None)
    result = await scanner_actions.nikto_scan("http://example.com")
    assert "error" in result
    assert "not found" in result["error"]


@pytest.mark.asyncio
async def test_sqlmap_not_found(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda x: None)
    result = await scanner_actions.sqlmap_scan("http://example.com/page?id=1")
    assert "error" in result
    assert "not found" in result["error"]
