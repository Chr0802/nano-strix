# tests/test_per_file_agent.py
import json
import threading
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class MockLLMResponse:
    def __init__(self, content):
        self.content = content
        self.tool_calls = []
        self.finish_reason = "stop"


@pytest.fixture
def mock_llm_client():
    client = MagicMock()
    client.chat = AsyncMock()
    return client


@pytest.fixture
def target_dir(tmp_path):
    """Create a minimal target directory structure."""
    (tmp_path / "src" / "auth").mkdir(parents=True)
    (tmp_path / "src" / "api").mkdir(parents=True)
    (tmp_path / "src" / "utils").mkdir(parents=True)
    (tmp_path / "tests").mkdir(parents=True)
    (tmp_path / "config").mkdir(parents=True)

    (tmp_path / "src" / "auth" / "login.py").write_text("def login(): pass")
    (tmp_path / "src" / "api" / "handler.py").write_text("def handler(): pass")
    (tmp_path / "src" / "utils" / "format.py").write_text("def fmt(): pass")
    (tmp_path / "tests" / "test_auth.py").write_text("def test(): pass")
    (tmp_path / "config" / "settings.py").write_text("DEBUG = True")
    return tmp_path


async def test_classify_files_returns_manifest(target_dir, mock_llm_client, tmp_path):
    from nano_strix.agents.per_file_lib.classifier import classify_files

    response_json = json.dumps({
        "files": {
            "src/auth/login.py": {"priority": "high", "dimensions": ["auth", "route"]},
            "src/api/handler.py": {"priority": "high", "dimensions": ["route", "dataflow"]},
            "src/utils/format.py": {"priority": "low", "dimensions": []},
            "tests/test_auth.py": {"priority": "low", "dimensions": []},
            "config/settings.py": {"priority": "low", "dimensions": []},
        }
    })
    mock_llm_client.chat.return_value = MockLLMResponse(response_json)

    manifest_path = tmp_path / "manifest.json"
    manifest = await classify_files(
        target_dir=str(target_dir),
        manifest_path=manifest_path,
        llm_client=mock_llm_client,
        agent_names=["route_agent", "dataflow_agent", "auth_agent", "dependency_agent"],
    )

    assert manifest is not None
    assert manifest.phase == "classification"
    assert len(manifest.files) == 5
    assert manifest.files["src/auth/login.py"].priority == "high"
    assert "auth" in manifest.files["src/auth/login.py"].dimensions
    assert manifest.files["src/utils/format.py"].priority == "low"
    assert manifest_path.exists()


async def test_classify_files_empty_directory(mock_llm_client, tmp_path):
    """Classify files on an empty directory should return an empty manifest."""
    from nano_strix.agents.per_file_lib.classifier import classify_files

    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()

    manifest_path = tmp_path / "manifest.json"
    manifest = await classify_files(
        target_dir=str(empty_dir),
        manifest_path=manifest_path,
        llm_client=mock_llm_client,
        agent_names=["route_agent", "dataflow_agent"],
    )

    assert manifest is not None
    assert manifest.phase == "classification"
    assert len(manifest.files) == 0
    # LLM should not be called for an empty directory
    mock_llm_client.chat.assert_not_called()


async def test_classify_files_missing_directory(mock_llm_client, tmp_path):
    """Classify files on a non-existent directory should raise FileNotFoundError."""
    from nano_strix.agents.per_file_lib.classifier import classify_files

    with pytest.raises(FileNotFoundError, match="Target directory not found"):
        await classify_files(
            target_dir="/nonexistent/path/12345",
            manifest_path=tmp_path / "manifest.json",
            llm_client=mock_llm_client,
            agent_names=["route_agent"],
        )


async def test_classify_files_invalid_json_fallback(target_dir, mock_llm_client, tmp_path):
    """LLM returns non-JSON; should fall back to all-medium classification."""
    from nano_strix.agents.per_file_lib.classifier import classify_files

    mock_llm_client.chat.return_value = MockLLMResponse("This is not JSON at all! Just some random text.")

    manifest_path = tmp_path / "manifest.json"
    manifest = await classify_files(
        target_dir=str(target_dir),
        manifest_path=manifest_path,
        llm_client=mock_llm_client,
        agent_names=["route_agent", "dataflow_agent", "auth_agent", "dependency_agent"],
    )

    assert manifest is not None
    assert len(manifest.files) == 5
    for mf in manifest.files.values():
        assert mf.priority == "medium", f"Expected 'medium' priority, got '{mf.priority}' for {mf.path}"
        assert mf.dimensions == [], f"Expected empty dimensions, got {mf.dimensions} for {mf.path}"


async def test_classify_files_markdown_fence_stripping(target_dir, mock_llm_client, tmp_path):
    """LLM wraps JSON in markdown code fences; fences should be stripped."""
    from nano_strix.agents.per_file_lib.classifier import classify_files

    json_body = json.dumps({
        "files": {
            "src/auth/login.py": {"priority": "high", "dimensions": ["auth"]},
            "src/api/handler.py": {"priority": "high", "dimensions": ["route"]},
            "src/utils/format.py": {"priority": "low", "dimensions": []},
            "tests/test_auth.py": {"priority": "low", "dimensions": []},
            "config/settings.py": {"priority": "medium", "dimensions": []},
        }
    })
    mock_llm_client.chat.return_value = MockLLMResponse(f"```json\n{json_body}\n```")

    manifest_path = tmp_path / "manifest.json"
    manifest = await classify_files(
        target_dir=str(target_dir),
        manifest_path=manifest_path,
        llm_client=mock_llm_client,
        agent_names=["route_agent", "dataflow_agent", "auth_agent", "dependency_agent"],
    )

    assert manifest is not None
    assert len(manifest.files) == 5
    assert manifest.files["src/auth/login.py"].priority == "high"
    assert manifest.files["src/auth/login.py"].dimensions == ["auth"]
    assert manifest.files["config/settings.py"].priority == "medium"


# ---------------------------------------------------------------------------
# Phase 2: Static scanner (semgrep / bandit)
# ---------------------------------------------------------------------------


@pytest.fixture
def sample_manifest(tmp_path, target_dir):
    from nano_strix.agents.per_file_lib.manifest import FileManifest

    files = {
        "src/auth/login.py": {"priority": "high", "dimensions": ["auth"]},
        "src/api/handler.py": {"priority": "high", "dimensions": ["route"]},
        "src/utils/format.py": {"priority": "low", "dimensions": []},
    }
    path = tmp_path / "manifest.json"
    agent_names = [
        "route_agent", "dataflow_agent", "auth_agent", "dependency_agent"
    ]
    return FileManifest.create(path, files, agent_names)


async def test_scanner_writes_findings_to_manifest(sample_manifest, target_dir):
    from nano_strix.agents.per_file_lib.scanner import run_static_scans

    await run_static_scans(
        manifest=sample_manifest,
        target_dir=str(target_dir),
        scanners=["semgrep"],
    )
    # After scanning, manifest should be updated (even if semgrep isn't installed,
    # the function should handle the missing tool gracefully)
    assert sample_manifest.phase == "static_scan"


async def test_scanner_missing_tool_handled(sample_manifest, target_dir):
    from nano_strix.agents.per_file_lib.scanner import run_static_scans

    await run_static_scans(
        manifest=sample_manifest,
        target_dir=str(target_dir),
        scanners=["nonexistent_tool_xyz"],
    )
    # Should not raise, should complete gracefully
    assert sample_manifest.phase == "static_scan"


# ---------------------------------------------------------------------------
# Phase 3: Sub-agent runner (multi-threaded agent_loop)
# ---------------------------------------------------------------------------


@pytest.fixture
def manifest_for_subagent(tmp_path):
    from nano_strix.agents.per_file_lib.manifest import FileManifest

    files = {
        "src/auth/login.py": {"priority": "high", "dimensions": ["auth", "route"]},
        "src/api/handler.py": {"priority": "high", "dimensions": ["route", "dataflow"]},
        "src/utils/format.py": {"priority": "low", "dimensions": []},
    }
    path = tmp_path / "manifest.json"
    agent_names = [
        "route_agent", "dataflow_agent", "auth_agent", "dependency_agent"
    ]
    return FileManifest.create(path, files, agent_names)


async def test_agent_loop_analyzes_matching_files(manifest_for_subagent):
    from nano_strix.agents.per_file_lib.sub_agents import SubAgentRunner

    mock_llm = MagicMock()
    mock_llm.chat = AsyncMock(return_value=MockLLMResponse(
        '{"findings": [{"id": "F-001", "title": "Test", "severity": "low"}]}'
    ))

    semaphore = threading.Semaphore(4)

    runner = SubAgentRunner(
        manifest=manifest_for_subagent,
        llm_client=mock_llm,
        semaphore=semaphore,
        target_dir="/tmp/test_target",
    )

    # Since run_single_agent tries to read files, we need to mock Path.read_text
    # to avoid file-not-found errors
    with patch('pathlib.Path.read_text', return_value="def foo(): pass"):
        runner.run_single_agent("route_agent", max_iterations=5)

    f = manifest_for_subagent.files["src/auth/login.py"]
    assert f.skip_votes.get("route_agent") is not None


async def test_agent_loop_votes_skip_on_non_matching(manifest_for_subagent):
    from nano_strix.agents.per_file_lib.sub_agents import SubAgentRunner

    mock_llm = MagicMock()
    mock_llm.chat = AsyncMock(return_value=MockLLMResponse('{"findings": []}'))

    semaphore = threading.Semaphore(4)

    runner = SubAgentRunner(
        manifest=manifest_for_subagent,
        llm_client=mock_llm,
        semaphore=semaphore,
        target_dir="/tmp/test_target",
    )

    with patch('pathlib.Path.read_text', return_value="def foo(): pass"):
        runner.run_single_agent("auth_agent", max_iterations=5)

    # Non-auth files should get skip votes from auth_agent
    f = manifest_for_subagent.files["src/utils/format.py"]
    assert f.skip_votes.get("auth_agent") == "skip"


async def test_sub_agent_runner_runs_all_threads(manifest_for_subagent):
    from nano_strix.agents.per_file_lib.sub_agents import SubAgentRunner

    mock_llm = MagicMock()
    mock_llm.chat = AsyncMock(return_value=MockLLMResponse(
        '{"findings": [{"id": "F-001", "title": "Test", "severity": "low"}]}'
    ))

    semaphore = threading.Semaphore(4)

    runner = SubAgentRunner(
        manifest=manifest_for_subagent,
        llm_client=mock_llm,
        semaphore=semaphore,
        target_dir="/tmp/test_target",
    )

    with patch('pathlib.Path.read_text', return_value="def foo(): pass"):
        runner.run_all(max_iterations=5, phase3_timeout=30)

    assert manifest_for_subagent.can_finish() is True
    for state in manifest_for_subagent.agents_state.values():
        assert state["status"] in ("completed", "pending")


async def test_failed_thread_restarts_agent(manifest_for_subagent):
    from nano_strix.agents.per_file_lib.sub_agents import SubAgentRunner

    semaphore = threading.Semaphore(4)

    class CrashingLLM:
        call_count = 0
        async def chat(self, *args, **kwargs):
            CrashingLLM.call_count += 1
            if CrashingLLM.call_count <= 1:
                raise RuntimeError("simulated crash")
            return MockLLMResponse('{"findings": []}')

    runner = SubAgentRunner(
        manifest=manifest_for_subagent,
        llm_client=CrashingLLM(),
        semaphore=semaphore,
        target_dir="/tmp/test_target",
        max_agent_restarts=2,
    )

    with patch('pathlib.Path.read_text', return_value="def foo(): pass"):
        runner.run_single_agent("route_agent", max_iterations=5)

    state = manifest_for_subagent.agents_state["route_agent"]
    assert state["restart_count"] >= 1
    assert state["status"] == "completed"


def test_health_check_detects_stale_agent(manifest_for_subagent):
    from datetime import datetime, timedelta, timezone

    from nano_strix.agents.per_file_lib.sub_agents import SubAgentRunner

    mock_llm = MagicMock()
    mock_llm.chat = AsyncMock()

    semaphore = threading.Semaphore(4)

    runner = SubAgentRunner(
        manifest=manifest_for_subagent,
        llm_client=mock_llm,
        semaphore=semaphore,
        target_dir="/tmp/test_target",
    )

    stale_time = datetime.now(timezone.utc) - timedelta(seconds=9999)
    manifest_for_subagent.update_agent_state("route_agent", {
        "status": "running",
        "last_health_check": stale_time.isoformat(),
    })

    unhealthy = runner.detect_unhealthy_agents(orphan_timeout_seconds=600)
    assert "route_agent" in unhealthy


# ---------------------------------------------------------------------------
# Integration test: full per_file.py entry point (mocked phases)
# ---------------------------------------------------------------------------


async def test_entry_point_runs_with_mocks(tmp_path, monkeypatch):
    """Full integration test of per_file agent via mocked phases."""
    import json
    from unittest.mock import AsyncMock, MagicMock

    # Create target directory structure
    target_dir = tmp_path / "test_target"
    target_dir.mkdir()
    (target_dir / "main.py").write_text("def main():\n    x = input()\n    exec(x)\n")
    (target_dir / "utils.py").write_text("def helper():\n    return 42\n")

    workspace = tmp_path / "tasks" / "t-001"
    workspace.mkdir(parents=True)

    manifest_path = workspace / "file_manifest.json"

    # Mock LLM client
    mock_llm = MagicMock()
    mock_llm.chat = AsyncMock()
    mock_llm.chat.side_effect = [
        # Phase 1 classification response
        MagicMock(content=json.dumps({
            "files": {
                "main.py": {"priority": "high", "dimensions": ["dataflow", "route"]},
                "utils.py": {"priority": "low", "dimensions": []},
            }
        }), tool_calls=[], finish_reason="stop"),
        # Phase 3: route_agent analysis (for utils.py, first claim)
        MagicMock(content=json.dumps({"findings": []}), tool_calls=[], finish_reason="stop"),
        # Phase 3: dataflow_agent analysis
        MagicMock(content=json.dumps({"findings": [
            {"id": "F-001", "title": "exec() injection", "severity": "critical",
             "category": "rce", "file_path": "main.py", "line_range": [1, 1],
             "description": "exec() with user input", "code_snippet": "exec(x)",
             "recommendation": "Do not use exec() with untrusted input", "confidence": 0.95}
        ]}), tool_calls=[], finish_reason="stop"),
        # Phase 3: auth_agent analysis
        MagicMock(content=json.dumps({"findings": []}), tool_calls=[], finish_reason="stop"),
        # Phase 3: dependency_agent analysis
        MagicMock(content=json.dumps({"findings": []}), tool_calls=[], finish_reason="stop"),
        # Additional calls as needed
        MagicMock(content=json.dumps({"findings": []}), tool_calls=[], finish_reason="stop"),
        MagicMock(content=json.dumps({"findings": []}), tool_calls=[], finish_reason="stop"),
    ]

    from nano_strix.agents.per_file_lib.classifier import classify_files
    from nano_strix.agents.per_file_lib.sub_agents import SubAgentRunner

    agent_names = ["route_agent", "dataflow_agent", "auth_agent", "dependency_agent"]

    # Phase 1: classify
    manifest = await classify_files(
        target_dir=str(target_dir),
        manifest_path=manifest_path,
        llm_client=mock_llm,
        agent_names=agent_names,
    )

    assert manifest.phase == "classification"
    assert len(manifest.files) == 2
    assert manifest.files["main.py"].priority == "high"

    # Phase 3: analysis (skip phase 2 scanner since tools likely not installed)
    manifest.phase = "analysis"
    manifest.save()

    analysis_llm = MagicMock()
    analysis_llm.chat = AsyncMock(return_value=MagicMock(
        content=json.dumps({"findings": []}),
        tool_calls=[], finish_reason="stop"
    ))

    runner = SubAgentRunner(
        manifest=manifest,
        llm_client=analysis_llm,
        semaphore=threading.Semaphore(4),
        target_dir=str(target_dir),
    )
    runner.run_all(max_iterations=5, phase3_timeout=30)

    assert manifest.can_finish() is True
