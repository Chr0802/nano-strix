"""End-to-end integration tests for the deep_analysis stage.

These tests exercise the full IPC round-trip: stdin input parsing,
RootAgent orchestration, and stdout IPC response. They are marked as
@pytest.mark.integration and run with a fake target directory.

When no valid LLM credentials are available, the stage gracefully
produces an error IPC response (which is still valid IPC output).
"""

import json
import io
import pytest


def _cleanup_globals():
    """Reset all graph module globals in-place (so existing references stay valid)."""
    import nano_strix.agents.deep_analysis_lib.graph as g

    g._agent_graph["nodes"].clear()
    g._agent_graph["edges"].clear()
    g._root_agent_id = None
    g._agent_messages.clear()
    g._running_agents.clear()
    g._agent_instances.clear()
    g._agent_states.clear()


@pytest.fixture(autouse=True)
def _reset_graph_globals():
    """Clean up graph globals before and after each integration test."""
    _cleanup_globals()
    yield
    _cleanup_globals()


@pytest.mark.integration
def test_deep_analysis_ipc_roundtrip(tmp_path, monkeypatch):
    """Full IPC round-trip with a target containing a Flask SQLi app."""
    target_dir = tmp_path / "test_app"
    target_dir.mkdir()
    (target_dir / "app.py").write_text("""from flask import Flask, request
import sqlite3

app = Flask(__name__)

@app.route('/login', methods=['POST'])
def login():
    user = request.form['username']
    pw = request.form['password']
    conn = sqlite3.connect('users.db')
    cursor = conn.cursor()
    cursor.execute(f"SELECT * FROM users WHERE name='{user}' AND password='{pw}'")
    return str(cursor.fetchone())
""")

    task_input = json.dumps({
        "type": "task",
        "task_id": "t-integration",
        "stage": "deep_analysis",
        "payload": {"target": str(target_dir), "stage_results": {}},
    })

    fake_stdin = io.StringIO(task_input + "\n")
    fake_stdout = io.StringIO()

    monkeypatch.setattr("sys.stdin", fake_stdin)
    monkeypatch.setattr("sys.stdout", fake_stdout)

    from nano_strix.agents.deep_analysis import main

    main()

    output = fake_stdout.getvalue().strip()
    assert output, "Should produce IPC output on stdout"

    result = json.loads(output)
    assert result["type"] == "result"
    assert result["task_id"] == "t-integration"
    assert result["payload"]["status"] in ("ok", "error")
    assert result["payload"]["stage"] == "deep_analysis"


@pytest.mark.integration
def test_deep_analysis_empty_input_produces_error():
    """Empty stdin should produce an immediate error response."""
    import io
    fake_stdin = io.StringIO("\n")
    fake_stdout = io.StringIO()

    import sys
    old_stdin = sys.stdin
    old_stdout = sys.stdout
    sys.stdin = fake_stdin
    sys.stdout = fake_stdout
    try:
        from nano_strix.agents.deep_analysis import main

        main()

        output = fake_stdout.getvalue().strip()
        assert output, "Should produce IPC output even on empty input"
        result = json.loads(output)
        assert result["type"] == "result"
        assert result["payload"]["status"] == "error"
        assert "No input" in result["payload"]["error"]
    finally:
        sys.stdin = old_stdin
        sys.stdout = old_stdout
