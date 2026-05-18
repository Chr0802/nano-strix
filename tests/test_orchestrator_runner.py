import json
import pytest
from pathlib import Path
from nano_strix.orchestrator.runner import OrchestratorRunner
from nano_strix.config.schema import AppConfig, PipelineConfig


@pytest.fixture
def workspace(tmp_path: Path):
    return tmp_path


@pytest.fixture
def runner(workspace):
    return OrchestratorRunner(workspace=workspace, config=AppConfig())


def test_runner_get_stages(runner):
    stages = runner.get_stages(PipelineConfig(stages=["per_file", "report"]))
    assert stages == ["per_file", "report"]


def test_runner_resolve_input(runner, workspace):
    findings = workspace / "external_findings.json"
    findings.write_text(json.dumps({"findings": []}))
    result = runner.resolve_input("findings", str(findings))
    assert result is not None


def test_runner_resolve_missing_input(runner):
    result = runner.resolve_input("findings", "/nonexistent/file.json")
    assert result is None
