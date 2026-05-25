import json
from pathlib import Path

from nano_strix.logging.llm_logger import LLMLogger
from nano_strix.logging.logger import JSONLLogger, LogEntry
from nano_strix.logging.task_logger import TaskLogger
from nano_strix.logging.tool_logger import ToolLogger


def test_log_entry_creation():
    entry = LogEntry(
        task_id="t-001",
        stage="per_file",
        category="task",
        level="info",
        event="task_started",
        data={"target": "src/auth.py"},
    )
    assert entry.task_id == "t-001"
    assert entry.category == "task"


def test_log_entry_to_json():
    entry = LogEntry(
        task_id="t-001",
        stage="per_file",
        category="llm",
        level="info",
        event="chat_request",
        data={"model": "claude-sonnet-4-6", "input_tokens": 100},
        duration=1.2,
    )
    j = entry.to_json()
    data = json.loads(j)
    assert data["task_id"] == "t-001"
    assert data["category"] == "llm"
    assert data["data"]["input_tokens"] == 100


def test_jsonl_logger_writes(tmp_path: Path):
    log_file = tmp_path / "test.jsonl"
    logger = JSONLLogger(log_file)
    entry = LogEntry(
        task_id="t-001",
        stage=None,
        category="task",
        level="info",
        event="created",
        data={},
    )
    logger.write(entry)
    lines = log_file.read_text().strip().split("\n")
    assert len(lines) == 1
    data = json.loads(lines[0])
    assert data["event"] == "created"


def test_task_logger(tmp_path: Path):
    log_file = tmp_path / "task.jsonl"
    logger = TaskLogger(log_file)
    logger.task_started("t-001", "per_file", {"target": "src/"})
    logger.task_completed("t-001", "per_file", {"findings_count": 3})

    lines = log_file.read_text().strip().split("\n")
    assert len(lines) == 2
    assert json.loads(lines[0])["event"] == "stage_started"
    assert json.loads(lines[1])["event"] == "stage_completed"


def test_llm_logger(tmp_path: Path):
    log_file = tmp_path / "llm.jsonl"
    logger = LLMLogger(log_file)
    logger.log_request("t-001", "per_file", "claude-sonnet-4-6", 5, 3)
    logger.log_response("t-001", "per_file", 2048, 512, 1200.0, "tool_calls")

    lines = log_file.read_text().strip().split("\n")
    assert len(lines) == 2
    assert json.loads(lines[0])["event"] == "llm_request"
    assert json.loads(lines[1])["event"] == "llm_response"


def test_tool_logger(tmp_path: Path):
    log_file = tmp_path / "tool.jsonl"
    logger = ToolLogger(log_file)
    logger.log_execution(
        "t-001",
        "per_file",
        "read_file",
        {"path": "src/a.py"},
        {"content": "file content here", "lines": 100},
        5.0,
    )

    lines = log_file.read_text().strip().split("\n")
    assert len(lines) == 1
    data = json.loads(lines[0])
    assert data["event"] == "tool_execution"
    assert data["data"]["tool"] == "read_file"
    assert data["data"]["result"] == {"content": "file content here", "lines": 100}
    assert data["data"]["result_chars"] > 0


def test_graph_logger_agent_created(tmp_path: Path):
    from nano_strix.logging.graph_logger import GraphLogger

    log_file = tmp_path / "graph.jsonl"
    logger = GraphLogger(log_file, task_id="t-001", stage="deep_analysis")
    logger.log_agent_created(
        agent_id="agent_abc123",
        parent_id="agent_root",
        name="Scanner-1",
        task="Scan files for vulnerabilities",
    )

    lines = log_file.read_text().strip().split("\n")
    assert len(lines) == 1
    data = json.loads(lines[0])
    assert data["task_id"] == "t-001"
    assert data["stage"] == "deep_analysis"
    assert data["category"] == "graph"
    assert data["event"] == "agent_created"
    assert data["data"]["agent_id"] == "agent_abc123"
    assert data["data"]["parent_id"] == "agent_root"
    assert data["data"]["name"] == "Scanner-1"


def test_graph_logger_status_change(tmp_path: Path):
    from nano_strix.logging.graph_logger import GraphLogger

    log_file = tmp_path / "graph.jsonl"
    logger = GraphLogger(log_file, task_id="t-001")
    logger.log_agent_status_change(
        agent_id="agent_abc123",
        old_status="running",
        new_status="waiting",
        reason="Waiting for cross-file analysis results",
    )

    lines = log_file.read_text().strip().split("\n")
    assert len(lines) == 1
    data = json.loads(lines[0])
    assert data["event"] == "agent_status_change"
    assert data["data"]["old_status"] == "running"
    assert data["data"]["new_status"] == "waiting"


def test_graph_logger_message_sent(tmp_path: Path):
    from nano_strix.logging.graph_logger import GraphLogger

    log_file = tmp_path / "graph.jsonl"
    logger = GraphLogger(log_file, task_id="t-001")
    logger.log_message_sent(
        from_id="agent_abc",
        to_id="agent_def",
        msg_id="msg_123",
        msg_type="information",
        priority="high",
    )

    lines = log_file.read_text().strip().split("\n")
    assert len(lines) == 1
    data = json.loads(lines[0])
    assert data["event"] == "message_sent"
    assert data["data"]["from"] == "agent_abc"
    assert data["data"]["to"] == "agent_def"


def test_graph_logger_agent_finished(tmp_path: Path):
    from nano_strix.logging.graph_logger import GraphLogger

    log_file = tmp_path / "graph.jsonl"
    logger = GraphLogger(log_file, task_id="t-001")
    logger.log_agent_finished(
        agent_id="agent_abc123",
        success=True,
        findings_count=3,
        result_summary="Found 3 vulnerabilities",
    )

    lines = log_file.read_text().strip().split("\n")
    assert len(lines) == 1
    data = json.loads(lines[0])
    assert data["event"] == "agent_finished"
    assert data["data"]["success"] is True
    assert data["data"]["findings_count"] == 3
