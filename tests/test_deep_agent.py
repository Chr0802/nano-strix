import pytest
from nano_strix.agents.per_file_lib.deep_agent import DeepAnalyseAgent
from nano_strix.agents.per_file_lib.graph import AgentState


class FakeLLM:
    """Fake LLM provider for testing agent loop."""
    def __init__(self, responses=None):
        self.responses = responses or []
        self.call_count = 0

    async def chat(self, messages, tools=None, temperature=0.7, max_tokens=4096):
        from nano_strix.llm.adapter import LLMResponse
        if self.call_count < len(self.responses):
            resp = self.responses[self.call_count]
            self.call_count += 1
            return resp
        return LLMResponse(content='{"findings": []}', finish_reason="stop")

    async def stream_chat(self, messages, tools=None, temperature=0.7, max_tokens=4096):
        yield '{"findings": []}'


def test_deep_agent_creation():
    state = AgentState(agent_name="TestAgent", task="test task", role="analyze")
    agent = DeepAnalyseAgent(state=state)
    assert agent.state.agent_name == "TestAgent"
    assert agent.state.task == "test task"


def test_check_agent_messages_resumes_waiting():
    from nano_strix.agents.per_file_lib.graph import _agent_messages, _agent_graph
    # Clean up
    from nano_strix.agents.per_file_lib import graph
    graph._agent_graph["nodes"].clear()
    graph._agent_messages.clear()

    state = AgentState(agent_name="TestAgent", task="test")
    state.enter_waiting_state()
    assert state.waiting_for_input is True

    _agent_graph["nodes"][state.agent_id] = {
        "id": state.agent_id, "name": "TestAgent", "task": "test",
        "status": "waiting", "parent_id": None, "created_at": "",
        "finished_at": None, "result": None, "role": "",
    }
    _agent_messages[state.agent_id] = [{
        "id": "msg_1", "from": "agent_xxx", "to": state.agent_id,
        "content": "<agent_completion_report><summary>done</summary></agent_completion_report>",
        "message_type": "information", "priority": "high", "timestamp": "",
        "delivered": True, "read": False,
    }]

    agent = DeepAnalyseAgent(state=state)
    agent._check_agent_messages()
    # Should have resumed from waiting
    assert state.waiting_for_input is False
