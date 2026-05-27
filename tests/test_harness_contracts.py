"""Unit tests for stage contracts and schema validator."""

from __future__ import annotations

from pathlib import Path

import pytest

from nano_strix.agents.deep_analysis_lib.contracts import (
    CONTRACTS,
    StageValidationResult,
    ValidationError,
    _has_source_files,
    _manifest_exists,
    _stage_artifact_exists,
    get_contract,
    validate_against_schema,
)


# ---------------------------------------------------------------------------
# validate_against_schema
# ---------------------------------------------------------------------------

class TestValidateAgainstSchema:
    def test_valid_object_passes(self):
        schema = {
            "type": "object",
            "required": ["name", "age"],
            "properties": {"name": {"type": "string"}, "age": {"type": "integer"}},
        }
        validate_against_schema({"name": "test", "age": 42}, schema)

    def test_missing_required_field_fails(self):
        schema = {
            "type": "object",
            "required": ["name", "age"],
            "properties": {"name": {"type": "string"}, "age": {"type": "integer"}},
        }
        with pytest.raises(ValidationError, match="required field missing"):
            validate_against_schema({"name": "test"}, schema)

    def test_wrong_type_fails(self):
        schema = {
            "type": "object",
            "required": ["name"],
            "properties": {"name": {"type": "string"}},
        }
        with pytest.raises(ValidationError, match="expected string"):
            validate_against_schema({"name": 42}, schema)

    def test_nested_object_validates(self):
        schema = {
            "type": "object",
            "required": ["data"],
            "properties": {
                "data": {
                    "type": "object",
                    "required": ["key"],
                    "properties": {"key": {"type": "string"}},
                }
            },
        }
        validate_against_schema({"data": {"key": "val"}}, schema)
        with pytest.raises(ValidationError, match="required field missing"):
            validate_against_schema({"data": {}}, schema)

    def test_array_items_validated(self):
        schema = {
            "type": "object",
            "required": ["items"],
            "properties": {
                "items": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["id"],
                        "properties": {"id": {"type": "integer"}},
                    },
                }
            },
        }
        validate_against_schema({"items": [{"id": 1}, {"id": 2}]}, schema)
        with pytest.raises(ValidationError, match=r"\[0\].id"):
            validate_against_schema({"items": [{"id": "not_int"}]}, schema)

    def test_top_level_not_object_passes_type_check(self):
        schema = {"type": "array", "items": {"type": "string"}}
        validate_against_schema(["a", "b", "c"], schema)

    def test_top_level_wrong_type_fails(self):
        schema = {"type": "array"}
        with pytest.raises(ValidationError, match="expected array"):
            validate_against_schema("not_a_list", schema)


# ---------------------------------------------------------------------------
# StageValidationResult
# ---------------------------------------------------------------------------

class TestStageValidationResult:
    def test_passed_result_format(self):
        r = StageValidationResult(passed=True, stage_name="scan", check_type="input")
        assert "passed" in r.to_message().lower()

    def test_failed_result_format(self):
        r = StageValidationResult(
            passed=False, stage_name="scan", check_type="output",
            errors=["Missing field: severity", "Missing field: location"],
        )
        msg = r.to_message()
        assert "FAILED" in msg
        assert "severity" in msg
        assert "location" in msg


# ---------------------------------------------------------------------------
# Input predicates
# ---------------------------------------------------------------------------

class TestInputPredicates:
    def test_has_source_files_passes(self, tmp_path: Path):
        (tmp_path / "main.py").write_text("print('hello')")
        assert _has_source_files(tmp_path) is True

    def test_has_source_files_fails_on_empty_dir(self, tmp_path: Path):
        assert _has_source_files(tmp_path) is False

    def test_manifest_exists_passes(self, tmp_path: Path):
        (tmp_path / "file_manifest.json").write_text("{}")
        assert _manifest_exists(tmp_path) is True

    def test_manifest_exists_fails(self, tmp_path: Path):
        assert _manifest_exists(tmp_path) is False

    def test_stage_artifact_exists_passes(self, tmp_path: Path):
        logs = tmp_path / "logs"
        logs.mkdir()
        (logs / "stage_scan_result.json").write_text("{}")
        check = _stage_artifact_exists("scan")
        assert check(tmp_path) is True

    def test_stage_artifact_exists_fails(self, tmp_path: Path):
        check = _stage_artifact_exists("scan")
        assert check(tmp_path) is False


# ---------------------------------------------------------------------------
# Stage contracts
# ---------------------------------------------------------------------------

class TestClassifyContract:
    def test_input_passes_with_source_files(self, tmp_path: Path):
        (tmp_path / "app.py").write_text("x = 1")
        contract = CONTRACTS["classify"]
        result = contract.check_input(tmp_path)
        assert result.passed

    def test_input_fails_on_empty_dir(self, tmp_path: Path):
        contract = CONTRACTS["classify"]
        result = contract.check_input(tmp_path)
        assert not result.passed

    def test_output_passes_with_valid_findings(self):
        contract = CONTRACTS["classify"]
        findings = [
            {"path": "a.py", "language": "python", "classification": "high"},
            {"path": "b.js", "language": "javascript", "classification": "medium"},
        ]
        result = contract.check_output(findings)
        assert result.passed

    def test_output_fails_missing_required_field(self):
        contract = CONTRACTS["classify"]
        findings = [{"path": "a.py", "language": "python"}]  # missing classification
        result = contract.check_output(findings)
        assert not result.passed
        assert "classification" in result.to_message()

    def test_output_fails_not_a_list(self):
        contract = CONTRACTS["classify"]
        result = contract.check_output({"not": "a list"})  # type: ignore[arg-type]
        assert not result.passed


class TestScanContract:
    def test_output_passes_with_valid_findings(self):
        contract = CONTRACTS["scan"]
        findings = [
            {
                "file_path": "a.py",
                "vulnerability_type": "xss",
                "severity": "high",
                "location": "line 10",
                "description": "Reflected XSS in user input",
            },
        ]
        result = contract.check_output(findings)
        assert result.passed

    def test_output_fails_missing_severity(self):
        contract = CONTRACTS["scan"]
        findings = [
            {"vulnerability_type": "xss", "location": "line 10", "description": "desc"},
        ]
        result = contract.check_output(findings)
        assert not result.passed
        assert "severity" in result.to_message()


class TestAnalyzeContract:
    def test_output_requires_all_fields(self):
        contract = CONTRACTS["analyze"]
        valid = [{
            "file": "a.py",
            "vulnerability_type": "sqli",
            "severity": "critical",
            "line_range": [10, 20],
            "description": "SQL injection",
            "exploitability": "E1",
        }]
        assert contract.check_output(valid).passed

        invalid = [{"file": "a.py", "severity": "high"}]
        assert not contract.check_output(invalid).passed


class TestCrossLinkContract:
    def test_output_requires_relation_fields(self):
        contract = CONTRACTS["cross-link"]
        valid = [{
            "related_findings": ["F-001", "F-002"],
            "relation_type": "attack_chain",
            "combined_severity": "critical",
        }]
        assert contract.check_output(valid).passed

        invalid = [{"related_findings": ["F-001"]}]
        assert not contract.check_output(invalid).passed


class TestReviewContract:
    def test_output_requires_report_fields(self):
        contract = CONTRACTS["review"]
        valid_report = {
            "executive_summary": "summary text",
            "findings": [],
            "recommendations": [],
            "coverage_report": {"total": 10},
        }
        result = contract.check_output([valid_report])
        assert result.passed

    def test_output_fails_missing_summary(self):
        contract = CONTRACTS["review"]
        invalid = [{"findings": [], "recommendations": [], "coverage_report": {}}]
        result = contract.check_output(invalid)
        assert not result.passed
        assert "executive_summary" in result.to_message()


# ---------------------------------------------------------------------------
# get_contract
# ---------------------------------------------------------------------------

class TestGetContract:
    def test_known_stages_return_contract(self):
        for name in ["classify", "scan", "analyze", "cross-link", "review"]:
            assert get_contract(name) is not None, f"Missing contract: {name}"

    def test_unknown_stage_returns_none(self):
        assert get_contract("nonexistent") is None
