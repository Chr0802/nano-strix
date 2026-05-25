import json
from pathlib import Path

from nano_strix.agents.deep_analysis_lib.graph import (
    AgentState,
    create_agent,
    send_message_to_agent,
    wait_for_message,
    agent_finish,
    set_graph_logger,
    _agent_graph,
    _agent_graph_lock,
)
from nano_strix.logging.graph_logger import GraphLogger


def test_create_agent_logs_graph_event(tmp_path: Path):
    """create_agent writes an agent_created event to graph.jsonl."""
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir(parents=True)

    # Clean up global state
    _agent_graph["nodes"].clear()
    _agent_graph["edges"].clear()

    graph_logger = GraphLogger(logs_dir / "graph.jsonl", task_id="t-test")
    set_graph_logger(graph_logger)

    parent_state = AgentState(
        agent_name="Parent",
        task="Delegate work",
        task_id="t-test",
        role="root",
    )
    # Register parent in graph
    with _agent_graph_lock:
        _agent_graph["nodes"][parent_state.agent_id] = {
            "id": parent_state.agent_id,
            "name": "Parent",
            "task": "Delegate work",
            "status": "running",
            "parent_id": None,
            "role": "root",
            "created_at": "",
            "finished_at": None,
            "result": None,
        }

    result = create_agent(parent_state, task="Scan files", name="Scanner")
    assert result["success"] is True

    lines = (logs_dir / "graph.jsonl").read_text().strip().split("\n")
    assert len(lines) >= 1

    event = json.loads(lines[0])
    assert event["event"] == "agent_created"
    assert event["data"]["name"] == "Scanner"
    assert event["data"]["parent_id"] == parent_state.agent_id


def test_send_message_logs_graph_event(tmp_path: Path):
    """send_message_to_agent writes a message_sent event."""
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir(parents=True)

    _agent_graph["nodes"].clear()
    _agent_graph["edges"].clear()

    graph_logger = GraphLogger(logs_dir / "graph.jsonl", task_id="t-test")
    set_graph_logger(graph_logger)

    sender = AgentState(agent_name="Sender", task="x", task_id="t-test", role="analyze")
    target = AgentState(agent_name="Target", task="y", task_id="t-test", role="analyze")

    with _agent_graph_lock:
        _agent_graph["nodes"][sender.agent_id] = {
            "id": sender.agent_id, "name": "Sender", "task": "x",
            "status": "running", "parent_id": None, "role": "analyze",
            "created_at": "", "finished_at": None, "result": None,
        }
        _agent_graph["nodes"][target.agent_id] = {
            "id": target.agent_id, "name": "Target", "task": "y",
            "status": "running", "parent_id": None, "role": "analyze",
            "created_at": "", "finished_at": None, "result": None,
        }

    result = send_message_to_agent(sender, target.agent_id, "hello")
    assert result["success"] is True

    lines = (logs_dir / "graph.jsonl").read_text().strip().split("\n")
    events = [json.loads(l)["event"] for l in lines]
    assert "message_sent" in events


def test_agent_finish_logs_graph_event(tmp_path: Path):
    """agent_finish writes an agent_finished event."""
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir(parents=True)

    _agent_graph["nodes"].clear()
    _agent_graph["edges"].clear()

    graph_logger = GraphLogger(logs_dir / "graph.jsonl", task_id="t-test")
    set_graph_logger(graph_logger)

    parent = AgentState(agent_name="Parent", task="x", task_id="t-test", role="root")
    child = AgentState(
        agent_name="Child", task="Analyze",
        parent_id=parent.agent_id, task_id="t-test", role="analyze",
    )

    with _agent_graph_lock:
        _agent_graph["nodes"][parent.agent_id] = {
            "id": parent.agent_id, "name": "Parent", "task": "x",
            "status": "running", "parent_id": None, "role": "root",
            "created_at": "", "finished_at": None, "result": None,
        }
        _agent_graph["nodes"][child.agent_id] = {
            "id": child.agent_id, "name": "Child", "task": "Analyze",
            "status": "running", "parent_id": parent.agent_id, "role": "analyze",
            "created_at": "", "finished_at": None, "result": None,
        }

    result = agent_finish(
        child,
        result_summary="Found 2 issues",
        findings=["issue1", "issue2"],
        success=True,
    )
    assert result["agent_completed"] is True

    lines = (logs_dir / "graph.jsonl").read_text().strip().split("\n")
    events = [json.loads(l)["event"] for l in lines]
    assert "agent_finished" in events
