"""Unit tests for FastAPI tool server — no Docker needed."""
import os
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def set_sandbox_mode():
    os.environ["NANO_STRIX_SANDBOX_MODE"] = "true"
    yield
    os.environ.pop("NANO_STRIX_SANDBOX_MODE", None)


@pytest.fixture
def client():
    from nano_strix.sandbox.tool_server import app
    return TestClient(app)


class TestHealthEndpoint:
    def test_health_returns_healthy(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "healthy"
        assert data["sandbox_mode"] is True


class TestTerminalExecute:
    def test_echo_command(self, client):
        resp = client.post(
            "/tools/terminal_execute",
            json={"command": "echo hello-world", "timeout": 10},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["exit_code"] == 0
        assert "hello-world" in data["stdout"]

    def test_timeout(self, client):
        resp = client.post(
            "/tools/terminal_execute",
            json={"command": "sleep 10", "timeout": 1},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["exit_code"] == -1
        assert "timed out" in data["stderr"].lower()

    def test_empty_body_uses_defaults(self, client):
        resp = client.post("/tools/terminal_execute", content=b"{}")
        assert resp.status_code == 200
        data = resp.json()
        assert "exit_code" in data


class TestFileRead:
    def test_existing_file(self, client, tmp_path):
        test_file = tmp_path / "test.txt"
        test_file.write_text("file content here")

        resp = client.post(
            "/tools/file_read",
            json={"path": str(test_file)},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["content"] == "file content here"
        assert data["size"] == 17

    def test_nonexistent_file(self, client):
        resp = client.post(
            "/tools/file_read",
            json={"path": "/nonexistent/path/file.txt"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "error" in data


class TestScannerEndpoints:
    def test_semgrep_not_installed_returns_error(self, client):
        resp = client.post(
            "/tools/scanner/semgrep",
            json={"target": "/tmp"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["exit_code"] == -1
        assert "error" in data

    def test_bandit_not_installed_returns_error(self, client):
        resp = client.post(
            "/tools/scanner/bandit",
            json={"target": "/tmp"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["exit_code"] == -1
        assert "error" in data

    def test_dynamic_route_unknown_scanner(self, client):
        resp = client.post(
            "/tools/scanner/unknown_scanner_xyz",
            json={"target": "/tmp"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "error" in data

    def test_dynamic_route_gitleaks(self, client):
        resp = client.post(
            "/tools/scanner/gitleaks",
            json={"target": "/tmp"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "error" in data or "exit_code" in data


class TestExecuteEndpoint:
    def test_execute_terminal(self, client):
        resp = client.post(
            "/execute",
            json={
                "tool_name": "terminal_execute",
                "kwargs": {"command": "echo from-execute", "timeout": 10},
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["exit_code"] == 0
        assert "from-execute" in data["stdout"]

    def test_execute_unknown_tool(self, client):
        resp = client.post(
            "/execute",
            json={"tool_name": "nonexistent_tool", "kwargs": {}},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["error"] is not None

    def test_execute_no_auth_when_token_not_configured(self, client):
        """Backward-compat: /execute works without auth when no token is set."""
        resp = client.post(
            "/execute",
            json={
                "tool_name": "terminal_execute",
                "kwargs": {"command": "echo no-auth", "timeout": 10},
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["exit_code"] == 0

    def test_execute_invalid_json_body(self, client):
        resp = client.post("/execute", content=b"not valid json")
        assert resp.status_code == 200
        data = resp.json()
        assert data["error"] is not None
