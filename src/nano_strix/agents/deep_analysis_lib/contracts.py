"""Stage contract definitions for harness validation.

Each stage defines input predicates (pre-conditions checked before
create_agent) and an output schema (validated during agent_finish).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from nano_strix.tools.registry import register_tool

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
    """Check that manifest exists and all file entries have priority and dimensions set."""
    import json
    manifest_path = workspace_root / "file_manifest.json"
    if not manifest_path.exists():
        return False
    try:
        data = json.loads(manifest_path.read_text())
        files = data.get("files", {})
        if not files:
            return False
        return all(
            f.get("priority") and f.get("dimensions")
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

MANIFEST_FILE_ENTRY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["file_path", "language", "priority", "dimensions", "status"],
    "properties": {
        "file_path": {"type": "string"},
        "language": {"type": "string"},
        "priority": {"type": "string"},
        "dimensions": {"type": "array"},
        "status": {"type": "string"},
        "scan_findings": {"type": "array"},
        "findings": {"type": "array"},
    },
}

MANIFEST_METADATA_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["created_at", "total_files"],
    "properties": {
        "created_at": {"type": "string"},
        "last_updated": {"type": "string"},
        "total_files": {"type": "integer"},
        "current_stage": {"type": "string"},
    },
}

CLASSIFY_FINDING_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["file_path", "language", "priority", "dimensions"],
    "properties": {
        "file_path": {"type": "string"},
        "language": {"type": "string"},
        "priority": {"type": "string"},
        "dimensions": {"type": "array"},
    },
}

CLASSIFY_OUTPUT: dict[str, Any] = {
    "type": "object",
    "required": ["findings"],
    "properties": {
        "findings": {"type": "array", "items": CLASSIFY_FINDING_SCHEMA},
    },
}

MANIFEST_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["metadata", "files"],
    "properties": {
        "metadata": MANIFEST_METADATA_SCHEMA,
        "files": {"type": "object"},
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
        "file_path": {"type": "string"},
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

REVIEW_FINDING_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["id", "title", "severity", "file_path", "description", "recommendation", "confidence"],
    "properties": {
        "id": {"type": "string"},
        "title": {"type": "string"},
        "severity": {"type": "string"},
        "exploitability": {"type": "string"},
        "nature": {"type": "string"},
        "category": {"type": "string"},
        "file_path": {"type": "string"},
        "line_range": {"type": "array"},
        "description": {"type": "string"},
        "code_snippet": {"type": "string"},
        "recommendation": {"type": "string"},
        "confidence": {"type": "string"},
    },
}

REVIEW_OUTPUT: dict[str, Any] = {
    "type": "object",
    "required": ["findings"],
    "properties": {
        "findings": {"type": "array", "items": REVIEW_FINDING_SCHEMA},
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
            "Your classify output failed validation. The `findings` parameter of "
            "agent_finish must be a LIST of OBJECTS (not strings). "
            "Each object MUST have: path, language, priority, dimensions.\n"
            "Example: [{{\"path\": \"src/foo.py\", \"language\": \"python\", "
            "\"priority\": \"high\", \"dimensions\": [\"auth\"]}}]\n"
            "Note: Do NOT include \"status\" in findings — that belongs in file_manifest.json only.\n"
            "Errors: {errors}"
        ),
    ),
    "scan": StageContract(
        stage_name="scan",
        input_predicates=[
            InputPredicate(
                description="Classify stage must have completed (logs/stage_classify_result.json)",
                check=_stage_artifact_exists("classify"),
            ),
            InputPredicate(
                description="file_manifest.json must exist with classified files (priority + dimensions set)",
                check=_manifest_has_classified_files,
            ),
        ],
        output_schema=SCAN_OUTPUT,
        max_retries=3,
        retry_prompt_template=(
            "Your scan output failed validation. The `findings` parameter of "
            "agent_finish must be a LIST of OBJECTS (not strings). "
            "Each object MUST have: vulnerability_type, severity, location, description.\n"
            "Example: [{{\"vulnerability_type\": \"hardcoded_secret\", \"severity\": \"HIGH\", "
            "\"location\": \"line 15\", \"description\": \"Hardcoded API key found\"}}]\n"
            "If no issues were found, pass an empty list: []\n"
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
            "Your analyze output failed validation. The `findings` parameter of "
            "agent_finish must be a LIST of OBJECTS (not strings). "
            "Each object MUST have: file, vulnerability_type, severity, line_range, description, exploitability.\n"
            "Example: [{{\"file_path\": \"src/foo.py\", \"vulnerability_type\": \"sql_injection\", "
            "\"severity\": \"HIGH\", \"line_range\": [10, 15], "
            "\"description\": \"User input interpolated into SQL query\", \"exploitability\": \"E3\"}}]\n"
            "If no vulnerabilities found, pass an empty list: []\n"
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
            "Your cross-link output failed validation. The `findings` parameter of "
            "agent_finish must be a LIST of OBJECTS (not strings). "
            "Each object MUST have: related_findings, relation_type, combined_severity.\n"
            "Example: [{{\"related_findings\": [\"ANALYZE-001\", \"ANALYZE-002\"], "
            "\"relation_type\": \"auth_bypass_chain\", \"combined_severity\": \"CRITICAL\"}}]\n"
            "If no attack chains found, pass an empty list: []\n"
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
            "Your review output failed validation. The `findings` parameter of "
            "agent_finish must be a LIST of OBJECTS (not strings). "
            "Each object MUST have: id, title, severity, file_path, description, recommendation, confidence.\n"
            "Example: [{{\"id\": \"REV-001\", \"title\": \"SQL Injection in login\", "
            "\"severity\": \"HIGH\", \"file_path\": \"src/login.py\", "
            "\"description\": \"User input interpolated into SQL query\", "
            "\"recommendation\": \"Use parameterized queries\", \"confidence\": \"HIGH\"}}]\n"
            "Use result_summary to describe excluded_findings and coverage_report.\n"
            "Errors: {errors}"
        ),
    ),
}


# ---------------------------------------------------------------------------
# Manifest tools (registered for LLM agent use)
# ---------------------------------------------------------------------------

def _resolve_workspace_root(agent_state: Any = None) -> Path | None:
    """Resolve workspace root from agent state or current context."""
    if agent_state is not None and hasattr(agent_state, 'task_id'):
        from nano_strix.tools.context import get_current_workspace_root
        ws = get_current_workspace_root()
        if ws:
            return Path(ws)
    from nano_strix.tools.context import get_current_workspace_root
    ws = get_current_workspace_root()
    return Path(ws) if ws else None


@register_tool
def read_manifest(
    agent_state: Any = None,
) -> dict[str, Any]:
    """Read file_manifest.json and return its contents with a summary.

    Returns the full manifest including metadata, file list with status
    breakdown, and per-file details. Use this to inspect the current
    state of the analysis pipeline.
    """
    import json

    ws = _resolve_workspace_root(agent_state)
    if ws is None:
        return {"success": False, "error": "No workspace root configured"}

    manifest_path = ws / "file_manifest.json"
    if not manifest_path.exists():
        return {
            "success": False,
            "error": f"file_manifest.json not found at {manifest_path}",
            "hint": "Run the classify stage first to create the manifest.",
        }

    try:
        data = json.loads(manifest_path.read_text())
    except json.JSONDecodeError as e:
        return {"success": False, "error": f"Invalid JSON in manifest: {e}"}

    files = data.get("files", {})
    metadata = data.get("metadata", {})

    # Build status breakdown
    status_counts: dict[str, int] = {}
    priority_counts: dict[str, int] = {}
    for f in files.values():
        st = f.get("status", "unknown")
        status_counts[st] = status_counts.get(st, 0) + 1
        pr = f.get("priority", "unknown")
        priority_counts[pr] = priority_counts.get(pr, 0) + 1

    return {
        "success": True,
        "manifest_path": str(manifest_path),
        "metadata": metadata,
        "summary": {
            "total_files": len(files),
            "by_status": status_counts,
            "by_priority": priority_counts,
        },
        "files": files,
    }


@register_tool
def check_coverage(
    agent_state: Any = None,
    stage: str = "",
) -> dict[str, Any]:
    """Check analysis coverage for a specific pipeline stage.

    Reads file_manifest.json and checks whether all required files have
    been processed for the given stage. Returns coverage stats and a list
    of files that still need processing.

    Stage-specific checks:
    - classify: all files have priority and dimensions set
    - scan: all files have scan_findings populated (status >= 'scanned')
    - analyze: all high/medium priority files have status 'analyzed' or 'skipped'
    """
    import json

    ws = _resolve_workspace_root(agent_state)
    if ws is None:
        return {"success": False, "error": "No workspace root configured"}

    manifest_path = ws / "file_manifest.json"
    if not manifest_path.exists():
        return {
            "success": False,
            "error": "file_manifest.json not found",
            "coverage_pct": 0.0,
        }

    try:
        data = json.loads(manifest_path.read_text())
    except json.JSONDecodeError as e:
        return {"success": False, "error": f"Invalid JSON in manifest: {e}"}

    files = data.get("files", {})
    total = len(files)
    if total == 0:
        return {"success": False, "error": "No files in manifest", "coverage_pct": 0.0}

    uncovered: list[str] = []
    stage_lower = stage.lower() if stage else ""

    if stage_lower == "classify":
        for path, f in files.items():
            if not f.get("priority") or not f.get("dimensions"):
                uncovered.append(path)
    elif stage_lower == "scan":
        for path, f in files.items():
            if f.get("status", "pending") == "pending":
                uncovered.append(path)
    elif stage_lower == "analyze":
        for path, f in files.items():
            prio = f.get("priority", "low")
            st = f.get("status", "pending")
            if prio in ("high", "medium") and st not in ("analyzed", "skipped", "linked", "reviewed"):
                uncovered.append(path)
    elif stage_lower == "cross-link":
        cross_link_path = ws / "cross_link_findings.json"
        if not cross_link_path.exists():
            uncovered.append("cross_link_findings.json (not created)")
    elif stage_lower == "review":
        for path, f in files.items():
            if f.get("priority") == "high" and f.get("status") not in ("analyzed", "reviewed"):
                uncovered.append(path)
    else:
        for path, f in files.items():
            if f.get("status", "pending") == "pending":
                uncovered.append(path)

    covered = total - len(uncovered)
    coverage_pct = (covered / total) * 100 if total > 0 else 0.0

    return {
        "success": True,
        "stage": stage or "general",
        "total_files": total,
        "covered": covered,
        "uncovered": uncovered,
        "coverage_pct": round(coverage_pct, 1),
        "ready_for_next_stage": len(uncovered) == 0,
        "recommendation": (
            "All files processed. Proceed to next stage."
            if len(uncovered) == 0
            else f"{len(uncovered)} file(s) still need processing: {uncovered[:10]}{'...' if len(uncovered) > 10 else ''}"
        ),
    }


@register_tool
def merge_manifest(
    agent_state: Any = None,
    child_manifest_path: str = "",
) -> dict[str, Any]:
    """Merge updates from a child/sub-agent manifest into the main file_manifest.json.

    Use this when a sub-agent produces partial manifest updates that need to
    be folded back into the primary manifest. Matching file paths are updated;
    new entries are added. The main manifest's metadata is refreshed.
    """
    import json
    from datetime import datetime, timezone

    ws = _resolve_workspace_root(agent_state)
    if ws is None:
        return {"success": False, "error": "No workspace root configured"}

    main_path = ws / "file_manifest.json"
    if not main_path.exists():
        return {"success": False, "error": "Main file_manifest.json not found"}

    child_path = Path(child_manifest_path)
    if not child_path.is_absolute():
        child_path = ws / child_path
    if not child_path.exists():
        return {"success": False, "error": f"Child manifest not found: {child_path}"}

    try:
        main_data = json.loads(main_path.read_text())
        child_data = json.loads(child_path.read_text())
    except json.JSONDecodeError as e:
        return {"success": False, "error": f"Invalid JSON: {e}"}

    main_files = main_data.setdefault("files", {})
    child_files = child_data.get("files", {})

    updated_count = 0
    added_count = 0
    for path, entry in child_files.items():
        if path in main_files:
            main_files[path].update(entry)
            updated_count += 1
        else:
            main_files[path] = entry
            added_count += 1

    # Refresh metadata
    metadata = main_data.setdefault("metadata", {})
    metadata["last_updated"] = datetime.now(timezone.utc).isoformat()
    metadata["total_files"] = len(main_files)

    main_path.write_text(json.dumps(main_data, indent=2, ensure_ascii=False))

    return {
        "success": True,
        "merged_from": str(child_path),
        "updated": updated_count,
        "added": added_count,
    }


# ---------------------------------------------------------------------------
# Contract registry helpers
# ---------------------------------------------------------------------------


def get_contract(stage_name: str) -> StageContract | None:
    """Look up a stage contract by name. Returns None if not found."""
    return CONTRACTS.get(stage_name)


def get_all_stage_names() -> list[str]:
    return list(CONTRACTS.keys())
