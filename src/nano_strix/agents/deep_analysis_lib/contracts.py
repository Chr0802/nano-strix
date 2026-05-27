"""Stage contract definitions for harness validation.

Each stage defines input predicates (pre-conditions checked before
create_agent) and an output schema (validated during agent_finish).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class StageValidationResult:
    """Result of a single validation check."""
    passed: bool
    stage_name: str = ""
    check_type: str = ""  # "input" | "output"
    errors: list[str] = field(default_factory=list)

    def to_message(self) -> str:
        """Human-readable error message for LLM consumption."""
        if self.passed:
            return f"[{self.stage_name}] {self.check_type} validation passed."
        lines = [f"[{self.stage_name}] {self.check_type} validation FAILED:"]
        for i, err in enumerate(self.errors, 1):
            lines.append(f"  {i}. {err}")
        return "\n".join(lines)


@dataclass
class InputPredicate:
    """A single pre-condition checked before a stage agent is created."""
    description: str
    check: Callable[[Path], bool]


@dataclass
class StageContract:
    """Full contract for one pipeline stage."""
    stage_name: str
    input_predicates: list[InputPredicate] = field(default_factory=list)
    output_schema: dict[str, Any] = field(default_factory=dict)
    max_retries: int = 3
    retry_prompt_template: str = ""

    def check_input(self, workspace_root: Path) -> StageValidationResult:
        errors: list[str] = []
        for pred in self.input_predicates:
            try:
                if not pred.check(workspace_root):
                    errors.append(f"Input predicate failed: {pred.description}")
            except Exception as exc:
                errors.append(f"Input predicate error ({pred.description}): {exc}")
        return StageValidationResult(
            passed=len(errors) == 0,
            stage_name=self.stage_name,
            check_type="input",
            errors=errors,
        )

    def check_output(self, findings: list[dict[str, Any]]) -> StageValidationResult:
        errors: list[str] = []
        if not isinstance(findings, list):
            return StageValidationResult(
                passed=False,
                stage_name=self.stage_name,
                check_type="output",
                errors=["findings must be a list"],
            )
        try:
            validate_against_schema({"findings": findings}, self.output_schema)
        except ValidationError as exc:
            errors = [str(exc)]
        return StageValidationResult(
            passed=len(errors) == 0,
            stage_name=self.stage_name,
            check_type="output",
            errors=errors,
        )


# ---------------------------------------------------------------------------
# Lightweight schema validator (no jsonschema dependency)
# ---------------------------------------------------------------------------

class ValidationError(Exception):
    pass


def validate_against_schema(data: Any, schema: dict[str, Any], path: str = "$") -> None:
    """Validate *data* against a simplified JSON Schema subset.

    Supported schema keywords: type, required, properties (objects),
    items (arrays).  Raises ``ValidationError`` on first failure.
    """
    expected_type = schema.get("type")
    if expected_type is not None:
        _check_type(data, expected_type, path)

    if expected_type == "object":
        required: list[str] = schema.get("required", [])
        properties: dict[str, dict[str, Any]] = schema.get("properties", {})

        for key in required:
            if key not in data:
                raise ValidationError(f"{path}.{key}: required field missing")

        for key, val in data.items():
            prop_schema = properties.get(key)
            if prop_schema is not None:
                validate_against_schema(val, prop_schema, f"{path}.{key}")

    elif expected_type == "array":
        item_schema = schema.get("items")
        if item_schema is not None:
            for i, item in enumerate(data):
                validate_against_schema(item, item_schema, f"{path}[{i}]")


def _check_type(value: Any, expected: str, path: str) -> None:
    type_map = {
        "string": str,
        "integer": int,
        "number": (int, float),
        "boolean": bool,
        "array": list,
        "object": dict,
    }
    py_type = type_map.get(expected)
    if py_type is None:
        return  # unknown type, skip check
    if not isinstance(value, py_type):
        raise ValidationError(
            f"{path}: expected {expected}, got {type(value).__name__}"
        )


# ---------------------------------------------------------------------------
# Stage-specific input predicates (pure functions)
# ---------------------------------------------------------------------------

def _has_source_files(workspace_root: Path) -> bool:
    """Check that the workspace contains at least one source file."""
    source_exts = {".py", ".js", ".ts", ".go", ".rs", ".java", ".rb", ".php", ".c", ".cpp"}
    for p in workspace_root.rglob("*"):
        if p.is_file() and p.suffix in source_exts:
            return True
    return False


def _manifest_exists(workspace_root: Path) -> bool:
    return (workspace_root / "file_manifest.json").exists()


def _manifest_has_classified_files(workspace_root: Path) -> bool:
    import json
    manifest_path = workspace_root / "file_manifest.json"
    if not manifest_path.exists():
        return False
    try:
        data = json.loads(manifest_path.read_text())
        files = data.get("files", {})
        return any(
            f.get("status", "pending") != "pending"
            for f in files.values()
        )
    except (json.JSONDecodeError, KeyError):
        return False


def _stage_artifact_exists(stage_name: str) -> Callable[[Path], bool]:
    def _check(workspace_root: Path) -> bool:
        return (workspace_root / "logs" / f"stage_{stage_name}_result.json").exists()
    return _check


def _all_stages_completed(workspace_root: Path) -> bool:
    for stage in ("classify", "scan", "analyze", "cross-link", "review"):
        if not (workspace_root / "logs" / f"stage_{stage}_result.json").exists():
            return False
    return True


# ---------------------------------------------------------------------------
# Output schemas (simplified JSON Schema dictionaries)
# ---------------------------------------------------------------------------

FILE_ENTRY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["path", "language", "classification"],
    "properties": {
        "path": {"type": "string"},
        "language": {"type": "string"},
        "classification": {"type": "string"},
    },
}

CLASSIFY_OUTPUT: dict[str, Any] = {
    "type": "object",
    "required": ["findings"],
    "properties": {
        "findings": {"type": "array", "items": FILE_ENTRY_SCHEMA},
    },
}

SCAN_FINDING_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["vulnerability_type", "severity", "location", "description"],
    "properties": {
        "file_path": {"type": "string"},
        "vulnerability_type": {"type": "string"},
        "severity": {"type": "string"},
        "location": {"type": "string"},
        "description": {"type": "string"},
    },
}

SCAN_OUTPUT: dict[str, Any] = {
    "type": "object",
    "required": ["findings"],
    "properties": {
        "findings": {"type": "array", "items": SCAN_FINDING_SCHEMA},
    },
}

ANALYZE_FINDING_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["file", "vulnerability_type", "severity", "line_range", "description", "exploitability"],
    "properties": {
        "file": {"type": "string"},
        "vulnerability_type": {"type": "string"},
        "severity": {"type": "string"},
        "line_range": {"type": "array"},
        "description": {"type": "string"},
        "exploitability": {"type": "string"},
    },
}

ANALYZE_OUTPUT: dict[str, Any] = {
    "type": "object",
    "required": ["findings"],
    "properties": {
        "findings": {"type": "array", "items": ANALYZE_FINDING_SCHEMA},
    },
}

CROSSLINK_FINDING_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["related_findings", "relation_type", "combined_severity"],
    "properties": {
        "related_findings": {"type": "array"},
        "relation_type": {"type": "string"},
        "combined_severity": {"type": "string"},
    },
}

CROSSLINK_OUTPUT: dict[str, Any] = {
    "type": "object",
    "required": ["findings"],
    "properties": {
        "findings": {"type": "array", "items": CROSSLINK_FINDING_SCHEMA},
    },
}

REPORT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["executive_summary", "findings", "recommendations", "coverage_report"],
    "properties": {
        "executive_summary": {"type": "string"},
        "findings": {"type": "array"},
        "recommendations": {"type": "array"},
        "coverage_report": {"type": "object"},
    },
}

REVIEW_OUTPUT: dict[str, Any] = {
    "type": "object",
    "required": ["findings"],
    "properties": {
        "findings": {"type": "array", "items": REPORT_SCHEMA},
    },
}


# ---------------------------------------------------------------------------
# Contract registry
# ---------------------------------------------------------------------------

CONTRACTS: dict[str, StageContract] = {
    "classify": StageContract(
        stage_name="classify",
        input_predicates=[InputPredicate(
            description="Workspace must contain at least one source file",
            check=_has_source_files,
        )],
        output_schema=CLASSIFY_OUTPUT,
        max_retries=3,
        retry_prompt_template=(
            "Your classify output failed validation. "
            "Ensure each file entry has: path, language, classification. "
            "Errors: {errors}"
        ),
    ),
    "scan": StageContract(
        stage_name="scan",
        input_predicates=[InputPredicate(
            description="file_manifest.json must exist in workspace",
            check=_manifest_exists,
        )],
        output_schema=SCAN_OUTPUT,
        max_retries=3,
        retry_prompt_template=(
            "Your scan output failed validation. "
            "Ensure each finding has: vulnerability_type, severity, location, description. "
            "Errors: {errors}"
        ),
    ),
    "analyze": StageContract(
        stage_name="analyze",
        input_predicates=[InputPredicate(
            description="Stage scan result artifact must exist (logs/stage_scan_result.json)",
            check=_stage_artifact_exists("scan"),
        )],
        output_schema=ANALYZE_OUTPUT,
        max_retries=3,
        retry_prompt_template=(
            "Your analyze output failed validation. "
            "Ensure each finding has: file, vulnerability_type, severity, line_range, description, exploitability. "
            "Errors: {errors}"
        ),
    ),
    "cross-link": StageContract(
        stage_name="cross-link",
        input_predicates=[InputPredicate(
            description="Stage analyze result artifact must exist (logs/stage_analyze_result.json)",
            check=_stage_artifact_exists("analyze"),
        )],
        output_schema=CROSSLINK_OUTPUT,
        max_retries=3,
        retry_prompt_template=(
            "Your cross-link output failed validation. "
            "Ensure each finding has: related_findings, relation_type, combined_severity. "
            "Errors: {errors}"
        ),
    ),
    "review": StageContract(
        stage_name="review",
        input_predicates=[InputPredicate(
            description="All previous stage result artifacts must exist",
            check=lambda ws: (
                _stage_artifact_exists("scan")(ws)
                and _stage_artifact_exists("analyze")(ws)
                and _stage_artifact_exists("cross-link")(ws)
            ),
        )],
        output_schema=REVIEW_OUTPUT,
        max_retries=3,
        retry_prompt_template=(
            "Your review output failed validation. "
            "Each report must have: executive_summary, findings, recommendations, coverage_report. "
            "Errors: {errors}"
        ),
    ),
}


def get_contract(stage_name: str) -> StageContract | None:
    """Look up a stage contract by name. Returns None if not found."""
    return CONTRACTS.get(stage_name)


def get_all_stage_names() -> list[str]:
    return list(CONTRACTS.keys())
