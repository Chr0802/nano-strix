import json

import pytest

from nano_strix.agents.base import BaseAgent, IPCMessage


def test_ipc_message_creation():
    msg = IPCMessage(type="task", task_id="t-001", payload={"files": ["a.py"]})
    assert msg.type == "task"
    assert msg.task_id == "t-001"


def test_ipc_message_to_json():
    msg = IPCMessage(type="result", task_id="t-001", payload={"findings": []})
    j = msg.to_json()
    data = json.loads(j)
    assert data["type"] == "result"
    assert data["task_id"] == "t-001"


def test_ipc_message_from_json():
    line = json.dumps(
        {"type": "progress", "task_id": "t-001", "detail": "analyzing..."}
    )
    msg = IPCMessage.from_json(line)
    assert msg.type == "progress"
    assert msg.detail == "analyzing..."


def test_base_agent_is_abstract():
    with pytest.raises(TypeError):
        BaseAgent()
