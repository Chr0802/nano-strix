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
