"""Integration tests for harness hooks with graph tools."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from nano_strix.agents.deep_analysis_lib.contracts import StageValidationResult
from nano_strix.agents.deep_analysis_lib.hooks import (
    clear_hooks,
    register_hook,
    run_post_agent_finish,
    run_pre_create_agent,
)
from nano_strix.agents.deep_analysis_lib.stage_state import (
    StageStatus,
    get_stage_state_manager,
    reset_stage_state_manager,
)


@pytest.fixture(autouse=True)
def _reset_harness_state():
    clear_hooks()
    reset_stage_state_manager()
    yield
    clear_hooks()
    reset_stage_state_manager()


class TestPreCreateAgentHook:
    def test_passes_when_input_conditions_met(self, tmp_path: Path, monkeypatch):
        (tmp_path / "file_manifest.json").write_text(
            '{"files": {"a.py": {"status": "classified"}}}'
        )
        monkeypatch.setattr(
            "nano_strix.agents.deep_analysis_lib.hooks.get_current_workspace_root",
            lambda: str(tmp_path),
        )
        result = run_pre_create_agent("StaticScanner", workspace_root=tmp_path)
        assert result.passed

    def test_fails_when_manifest_missing(self, tmp_path: Path, monkeypatch):
        monkeypatch.setattr(
            "nano_strix.agents.deep_analysis_lib.hooks.get_current_workspace_root",
            lambda: str(tmp_path),
        )
        result = run_pre_create_agent("StaticScanner", workspace_root=tmp_path)
        assert not result.passed
        assert "file_manifest" in result.to_message().lower()

    def test_unknown_agent_name_passes_without_contract(self, tmp_path: Path):
        result = run_pre_create_agent("SomeRandomAgent", workspace_root=tmp_path)
        assert result.passed  # no contract = no validation

    def test_sets_stage_to_in_progress_on_pass(self, tmp_path: Path, monkeypatch):
        (tmp_path / "file_manifest.json").write_text(
            '{"files": {"a.py": {"status": "classified"}}}'
        )
        monkeypatch.setattr(
            "nano_strix.agents.deep_analysis_lib.hooks.get_current_workspace_root",
            lambda: str(tmp_path),
        )
        run_pre_create_agent("StaticScanner", workspace_root=tmp_path)
        sm = get_stage_state_manager()
        stage = sm.get("scan")
        assert stage is not None
        assert stage.status == StageStatus.IN_PROGRESS

    def test_registered_hook_is_called(self, tmp_path: Path):
        called = []

        def my_hook(agent_name, workspace_root, **kwargs):
            called.append(agent_name)
            return StageValidationResult(
                passed=True, stage_name=agent_name, check_type="input"
            )

        register_hook("pre_create_agent", my_hook)
        run_pre_create_agent("TestAgent", workspace_root=tmp_path)
        assert "TestAgent" in called


class TestPostAgentFinishHook:
    def test_passes_with_valid_findings(self, tmp_path: Path, monkeypatch):
        monkeypatch.setattr(
            "nano_strix.agents.deep_analysis_lib.hooks.get_current_workspace_root",
            lambda: str(tmp_path),
        )
        findings = [
            {
                "file_path": "a.py",
                "vulnerability_type": "xss",
                "severity": "high",
                "location": "line 10",
                "description": "desc",
            },
        ]
        result = run_post_agent_finish(
            agent_name="StaticScanner",
            agent_id="agent_test_1",
            findings=findings,
            workspace_root=tmp_path,
        )
        assert result.passed
        sm = get_stage_state_manager()
        stage = sm.get("scan")
        assert stage is not None
        assert stage.status == StageStatus.COMPLETED

    def test_fails_and_increments_retry(self, tmp_path: Path, monkeypatch):
        monkeypatch.setattr(
            "nano_strix.agents.deep_analysis_lib.hooks.get_current_workspace_root",
            lambda: str(tmp_path),
        )
        invalid_findings = [{"bad": "data"}]  # missing all required fields
        result = run_post_agent_finish(
            agent_name="StaticScanner",
            agent_id="agent_test_2",
            findings=invalid_findings,
            workspace_root=tmp_path,
        )
        assert not result.passed
        assert "Retry" in result.to_message()
        sm = get_stage_state_manager()
        stage = sm.get("scan")
        assert stage.retry_counts.get("agent_test_2", 0) == 1
        assert stage.status == StageStatus.IN_PROGRESS

    def test_retries_exhausted_after_max(self, tmp_path: Path, monkeypatch):
        monkeypatch.setattr(
            "nano_strix.agents.deep_analysis_lib.hooks.get_current_workspace_root",
            lambda: str(tmp_path),
        )
        invalid = [{"bad": "data"}]
        agent_id = "agent_test_3"
        for _ in range(4):  # max_retries=3 → 4th call exhausts
            result = run_post_agent_finish(
                agent_name="StaticScanner",
                agent_id=agent_id,
                findings=invalid,
                max_retries=3,
                workspace_root=tmp_path,
            )
        assert not result.passed
        assert "ALL RETRIES EXHAUSTED" in result.to_message()
        sm = get_stage_state_manager()
        stage = sm.get("scan")
        assert stage.status == StageStatus.FAILED

    def test_persists_stage_result_on_success(self, tmp_path: Path, monkeypatch):
        monkeypatch.setattr(
            "nano_strix.agents.deep_analysis_lib.hooks.get_current_workspace_root",
            lambda: str(tmp_path),
        )
        findings = [
            {
                "file_path": "a.py",
                "vulnerability_type": "xss",
                "severity": "high",
                "location": "line 10",
                "description": "desc",
            },
        ]
        run_post_agent_finish(
            agent_name="StaticScanner",
            agent_id="agent_test_4",
            findings=findings,
            workspace_root=tmp_path,
        )
        artifact = tmp_path / "logs" / "stage_scan_result.json"
        assert artifact.exists()
        data = json.loads(artifact.read_text())
        assert data["stage"] == "scan"
        assert len(data["findings"]) == 1


class TestStageStateLifecycle:
    def test_full_lifecycle_pending_to_completed(self):
        sm = get_stage_state_manager()
        sm.transition("scan", StageStatus.IN_PROGRESS, "started")
        sm.transition("scan", StageStatus.VALIDATING, "checking output")
        sm.transition("scan", StageStatus.COMPLETED, "all good")
        stage = sm.get("scan")
        assert stage is not None
        assert stage.status == StageStatus.COMPLETED

    def test_full_lifecycle_pending_to_failed(self):
        sm = get_stage_state_manager()
        sm.transition("analyze", StageStatus.IN_PROGRESS, "started")
        sm.transition("analyze", StageStatus.FAILED, "retries exhausted")
        stage = sm.get("analyze")
        assert stage is not None
        assert stage.status == StageStatus.FAILED

    def test_all_completed(self):
        sm = get_stage_state_manager()
        for name in ["classify", "scan", "analyze", "cross-link", "review"]:
            sm.transition(name, StageStatus.COMPLETED, "done")
        assert sm.all_completed() is True

    def test_not_all_completed(self):
        sm = get_stage_state_manager()
        sm.transition("classify", StageStatus.COMPLETED, "done")
        sm.transition("scan", StageStatus.IN_PROGRESS, "working")
        assert sm.all_completed() is False
