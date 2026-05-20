# tests/test_per_file_agent.py
import json
import pytest
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock


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
