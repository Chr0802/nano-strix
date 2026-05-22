import pytest
from nano_strix.agents.per_file_lib.graph import (
    AgentState,
    create_agent,
    wait_for_message,
    send_message_to_agent,
    agent_finish,
    view_agent_graph,
    _agent_graph,
    _agent_messages,
    _running_agents,
)


def _cleanup_graph():
    """Reset global state between tests (mutate in-place to avoid stale references)."""
    import nano_strix.agents.per_file_lib.graph as g
    g._agent_graph["nodes"].clear()
    g._agent_graph["edges"].clear()
    g._root_agent_id = None
    g._agent_messages.clear()
    g._running_agents.clear()
    g._agent_instances.clear()
    g._agent_states.clear()


class TestCreateAgent:
    def test_create_agent_returns_agent_id(self):
        _cleanup_graph()
        state = AgentState(agent_name="TestRoot")
        result = create_agent(state, task="analyze login.py", name="LoginAnalyzer")
        assert result["success"] is True
        assert result["agent_id"].startswith("agent_")
        assert result["agent_info"]["name"] == "LoginAnalyzer"

    def test_create_agent_registers_node(self):
        _cleanup_graph()
        state = AgentState(agent_name="TestRoot")
        result = create_agent(state, task="scan dir", name="Scanner")
        agent_id = result["agent_id"]
        assert agent_id in _agent_graph["nodes"]
        node = _agent_graph["nodes"][agent_id]
        assert node["name"] == "Scanner"
        assert node["task"] == "scan dir"
        assert node["status"] == "running"

    def test_create_agent_adds_delegation_edge(self):
        _cleanup_graph()
        state = AgentState(agent_name="Parent")
        result = create_agent(state, task="child task", name="Child")
        delegation_edges = [e for e in _agent_graph["edges"] if e["type"] == "delegation"]
        assert any(e["from"] == state.agent_id and e["to"] == result["agent_id"]
                   for e in delegation_edges)

    def test_create_agent_no_inherit_context(self):
        _cleanup_graph()
        state = AgentState(agent_name="Parent")
        state.add_message("user", "secret context")
        result = create_agent(state, task="fresh task", name="FreshChild", inherit_context=False)
        assert result["success"] is True


class TestSendMessage:
    def test_send_message_to_agent(self):
        _cleanup_graph()
        sender = AgentState(agent_name="Sender")
        receiver = AgentState(agent_name="Receiver")
        _agent_graph["nodes"][receiver.agent_id] = {
            "id": receiver.agent_id, "name": "Receiver", "task": "wait",
            "status": "running", "parent_id": None, "created_at": "",
            "finished_at": None, "result": None, "role": "",
        }
        _agent_messages[receiver.agent_id] = []

        result = send_message_to_agent(sender, receiver.agent_id, "hello", "information", "normal")
        assert result["success"] is True
        assert result["delivery_status"] == "delivered"
        assert len(_agent_messages[receiver.agent_id]) == 1
        msg = _agent_messages[receiver.agent_id][0]
        assert msg["content"] == "hello"
        assert msg["from"] == sender.agent_id
        assert msg["read"] is False

    def test_send_message_unknown_target(self):
        _cleanup_graph()
        sender = AgentState(agent_name="Sender")
        result = send_message_to_agent(sender, "nonexistent", "hi")
        assert result["success"] is False


class TestWaitForMessage:
    def test_wait_for_message_sets_waiting(self):
        _cleanup_graph()
        state = AgentState(agent_name="Waiter")
        _agent_graph["nodes"][state.agent_id] = {
            "id": state.agent_id, "name": "Waiter", "task": "test",
            "status": "running", "parent_id": None, "created_at": "",
            "finished_at": None, "result": None, "role": "",
        }
        result = wait_for_message(state, reason="test wait")
        assert result["success"] is True
        assert result["status"] == "waiting"
        assert state.waiting_for_input is True
        assert _agent_graph["nodes"][state.agent_id]["status"] == "waiting"


class TestAgentFinish:
    def test_agent_finish_root_agent_rejected(self):
        _cleanup_graph()
        state = AgentState(agent_name="Root", parent_id=None)
        _agent_graph["nodes"][state.agent_id] = {
            "id": state.agent_id, "name": "Root", "task": "root task",
            "status": "running", "parent_id": None, "created_at": "",
            "finished_at": None, "result": None, "role": "",
        }
        result = agent_finish(state, result_summary="done")
        assert result["agent_completed"] is False
        assert "subagents" in result.get("error", "").lower()

    def test_agent_finish_notifies_parent(self):
        _cleanup_graph()
        parent = AgentState(agent_name="Parent")
        child = AgentState(agent_name="Child", parent_id=parent.agent_id)
        _agent_graph["nodes"][parent.agent_id] = {
            "id": parent.agent_id, "name": "Parent", "task": "parent task",
            "status": "running", "parent_id": None, "created_at": "",
            "finished_at": None, "result": None, "role": "",
        }
        _agent_graph["nodes"][child.agent_id] = {
            "id": child.agent_id, "name": "Child", "task": "child task",
            "status": "running", "parent_id": parent.agent_id, "created_at": "",
            "finished_at": None, "result": None, "role": "",
        }
        _agent_messages[parent.agent_id] = []

        result = agent_finish(child, result_summary="done", findings=["F-1"],
                             success=True, report_to_parent=True)
        assert result["agent_completed"] is True
        assert result["parent_notified"] is True
        assert len(_agent_messages[parent.agent_id]) == 1
        report = _agent_messages[parent.agent_id][0]["content"]
        assert "<agent_completion_report>" in report
        assert "done" in report
        assert _agent_graph["nodes"][child.agent_id]["status"] == "finished"

    def test_agent_finish_no_report_to_parent(self):
        _cleanup_graph()
        parent = AgentState(agent_name="Parent")
        child = AgentState(agent_name="Child", parent_id=parent.agent_id)
        _agent_graph["nodes"][parent.agent_id] = {
            "id": parent.agent_id, "name": "Parent", "task": "parent task",
            "status": "running", "parent_id": None, "created_at": "",
            "finished_at": None, "result": None, "role": "",
        }
        _agent_graph["nodes"][child.agent_id] = {
            "id": child.agent_id, "name": "Child", "task": "child task",
            "status": "running", "parent_id": parent.agent_id, "created_at": "",
            "finished_at": None, "result": None, "role": "",
        }
        _agent_messages[parent.agent_id] = []

        result = agent_finish(child, result_summary="silent done", report_to_parent=False)
        assert result["parent_notified"] is False
