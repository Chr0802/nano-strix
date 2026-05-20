# tests/test_per_file_agent.py
import asyncio
import json

import pytest
from unittest.mock import AsyncMock, MagicMock

pytestmark = pytest.mark.asyncio


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
