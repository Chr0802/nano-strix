from __future__ import annotations

from pathlib import Path

import pytest

from nano_strix.agents.manager import AgentManager
from nano_strix.config.schema import IPCConfig


@pytest.fixture
def manager(tmp_path: Path):
    return AgentManager(workspace=tmp_path, config=IPCConfig(timeout_seconds=5))


@pytest.mark.asyncio
async def test_manager_dispatch_and_receive(manager, tmp_path: Path):
    """Test sending a task to a mock agent script and receiving result."""
    script = tmp_path / "mock_agent.py"
    script.write_text("""
import sys, json
line = sys.stdin.readline()
msg = json.loads(line)
result = {
    "type": "result",
    "task_id": msg["task_id"],
    "payload": {"findings": ["f-001"]},
}
print(json.dumps(result))
""")

    result = await manager.dispatch(
        agent_script=str(script),
        task_id="t-001",
        stage="per_file",
        payload={"files": ["a.py"]},
    )
    assert result["findings"] == ["f-001"]


@pytest.mark.asyncio
async def test_manager_timeout(manager, tmp_path: Path):
    """Test that dispatch times out for a slow agent."""
    script = tmp_path / "slow_agent.py"
    script.write_text("import time; time.sleep(60)")

    result = await manager.dispatch(
        agent_script=str(script),
        task_id="t-002",
        stage="per_file",
        payload={},
    )
    assert "error" in result or "timeout" in str(result).lower()
