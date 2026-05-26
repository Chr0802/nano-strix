# Deep Analysis Harness Mechanism Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add stage-level contract validation and progress tracking to the 5-phase deep analysis pipeline via graph-tool middleware hooks.

**Architecture:** Three new modules — `contracts.py` (stage input/output schemas), `stage_state.py` (progress tracking), `hooks.py` (hook execution + retry logic) — plug into existing `graph.py` `create_agent` and `agent_finish` tools. No prompt changes, no new agent types.

**Tech Stack:** Python 3.10+, dataclasses, threading.Lock, no new dependencies.

**Spec:** `docs/superpowers/specs/2026-05-26-deep-analysis-harness-design.md`

---

## File Structure

| File | Responsibility |
|------|---------------|
| `agents/deep_analysis_lib/contracts.py` (NEW) | `InputPredicate`, `StageContract`, `StageValidationResult`, `validate_against_schema`, 5 stage + root contract definitions |
| `agents/deep_analysis_lib/stage_state.py` (NEW) | `StageStatus`, `StageProgress`, `StageStateManager` singleton |
| `agents/deep_analysis_lib/hooks.py` (NEW) | `HarnessHooks` — registration, `run_pre_create_agent`, `run_post_agent_finish`, retry tracking |
| `agents/deep_analysis_lib/graph.py` (MODIFY) | `_HOOKS` registry, call hooks in `create_agent` / `agent_finish`, enhance `view_agent_graph` |
| `agents/deep_analysis.py` (MODIFY) | Initialize `HarnessHooks` with default contracts, register hooks |
| `logging/graph_logger.py` (MODIFY) | Add `stage_name`, `checkpoint_detail` to `log_agent_status_change`; add `validation_result`, `schema_errors` to `log_agent_finished` |
| `tests/test_harness_contracts.py` (NEW) | Unit tests for schema validator + each stage contract |
| `tests/test_harness_hooks.py` (NEW) | Integration tests for hook execution + retry lifecycle |

---

### Task 1: Create contracts.py — data classes, schema validator, 5-stage definitions

**Files:**
- Create: `src/nano_strix/agents/deep_analysis_lib/contracts.py`

- [ ] **Step 1: Write the file with all data classes and stage contract definitions**

```python
"""Stage contract definitions for harness validation.

Each stage defines input predicates (pre-conditions checked before
create_agent) and an output schema (validated during agent_finish).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable


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
```

- [ ] **Step 2: Run a quick import check to verify the file is syntactically correct**

Run: `cd /Users/chenhaoran/workspace/agent/nano-strix && .venv/bin/python -c "from nano_strix.agents.deep_analysis_lib.contracts import CONTRACTS, validate_against_schema; print('OK:', len(CONTRACTS), 'contracts')"`
Expected: `OK: 5 contracts`

- [ ] **Step 3: Commit**

```bash
git add src/nano_strix/agents/deep_analysis_lib/contracts.py
git commit -m "feat: add StageContract definitions and schema validator for harness

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 2: Create stage_state.py — progress tracking

**Files:**
- Create: `src/nano_strix/agents/deep_analysis_lib/stage_state.py`

- [ ] **Step 1: Write the file**

```python
"""Stage-level progress tracking for the deep analysis harness."""

from __future__ import annotations

import threading
import time as _time
from dataclasses import dataclass, field
from enum import Enum


class StageStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    VALIDATING = "validating"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class StageProgress:
    stage_name: str
    status: StageStatus = StageStatus.PENDING
    agent_ids: list[str] = field(default_factory=list)
    retry_counts: dict[str, int] = field(default_factory=dict)
    started_at: float | None = None
    completed_at: float | None = None
    last_checkpoint: str = ""
    artifacts: list[str] = field(default_factory=list)

    def register_agent(self, agent_id: str) -> None:
        if agent_id not in self.agent_ids:
            self.agent_ids.append(agent_id)
        self.retry_counts.setdefault(agent_id, 0)

    def increment_retry(self, agent_id: str) -> int:
        self.retry_counts[agent_id] = self.retry_counts.get(agent_id, 0) + 1
        return self.retry_counts[agent_id]

    def all_agents_finished(self, finished_agent_ids: set[str]) -> bool:
        return set(self.agent_ids).issubset(finished_agent_ids)

    def is_terminal(self) -> bool:
        return self.status in (StageStatus.COMPLETED, StageStatus.FAILED)


class StageStateManager:
    """Thread-safe singleton manager for stage progress tracking."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._stages: dict[str, StageProgress] = {}

    def get_or_create(self, stage_name: str) -> StageProgress:
        with self._lock:
            if stage_name not in self._stages:
                self._stages[stage_name] = StageProgress(stage_name=stage_name)
            return self._stages[stage_name]

    def get(self, stage_name: str) -> StageProgress | None:
        with self._lock:
            return self._stages.get(stage_name)

    def transition(
        self,
        stage_name: str,
        to_status: StageStatus,
        checkpoint_detail: str = "",
    ) -> StageProgress:
        with self._lock:
            sp = self.get_or_create(stage_name)
            sp.status = to_status
            sp.last_checkpoint = checkpoint_detail
            if to_status == StageStatus.IN_PROGRESS and sp.started_at is None:
                sp.started_at = _time.monotonic()
            if to_status == StageStatus.COMPLETED:
                sp.completed_at = _time.monotonic()
            if to_status == StageStatus.FAILED:
                sp.completed_at = _time.monotonic()
            return sp

    def add_artifact(self, stage_name: str, artifact_path: str) -> None:
        with self._lock:
            sp = self.get_or_create(stage_name)
            if artifact_path not in sp.artifacts:
                sp.artifacts.append(artifact_path)

    def all_completed(self) -> bool:
        with self._lock:
            for sp in self._stages.values():
                if sp.status != StageStatus.COMPLETED:
                    return False
            return True

    def to_dict(self) -> dict[str, dict]:
        with self._lock:
            result: dict[str, dict] = {}
            for name, sp in self._stages.items():
                result[name] = {
                    "status": sp.status.value,
                    "agent_count": len(sp.agent_ids),
                    "retry_counts": dict(sp.retry_counts),
                    "started_at": sp.started_at,
                    "completed_at": sp.completed_at,
                    "last_checkpoint": sp.last_checkpoint,
                    "artifacts": list(sp.artifacts),
                }
            return result

    def reset(self) -> None:
        with self._lock:
            self._stages.clear()


# Module-level singleton
_stage_state_manager: StageStateManager | None = None
_lock = threading.Lock()


def get_stage_state_manager() -> StageStateManager:
    global _stage_state_manager
    with _lock:
        if _stage_state_manager is None:
            _stage_state_manager = StageStateManager()
        return _stage_state_manager


def reset_stage_state_manager() -> None:
    global _stage_state_manager
    with _lock:
        if _stage_state_manager is not None:
            _stage_state_manager.reset()
        _stage_state_manager = None
```

- [ ] **Step 2: Verify import**

Run: `cd /Users/chenhaoran/workspace/agent/nano-strix && .venv/bin/python -c "from nano_strix.agents.deep_analysis_lib.stage_state import get_stage_state_manager, StageStatus; m = get_stage_state_manager(); m.transition('test', StageStatus.COMPLETED, 'done'); print('OK:', m.to_dict())"`
Expected: `OK: {'test': {'status': 'completed', ...}}`

- [ ] **Step 3: Commit**

```bash
git add src/nano_strix/agents/deep_analysis_lib/stage_state.py
git commit -m "feat: add StageStateManager for stage-level progress tracking

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 3: Create hooks.py — hook execution and retry management

**Files:**
- Create: `src/nano_strix/agents/deep_analysis_lib/hooks.py`

- [ ] **Step 1: Write the file**

```python
"""Harness hooks: registration, execution, and retry management.

Hooks are callables inserted into ``create_agent`` (pre-hook) and
``agent_finish`` (post-hook). They run synchronously in the caller's
thread and return ``StageValidationResult``.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any, Callable

from nano_strix.agents.deep_analysis_lib.contracts import (
    CONTRACTS,
    StageContract,
    StageValidationResult,
    get_contract,
)
from nano_strix.agents.deep_analysis_lib.stage_state import (
    StageStatus,
    get_stage_state_manager,
)
from nano_strix.tools.context import get_current_workspace_root

_HOOKS: dict[str, list[Callable]] = {
    "pre_create_agent": [],
    "post_agent_finish": [],
    "pre_root_finish": [],
}
_hooks_lock = threading.Lock()


class RetryExhaustedError(Exception):
    """Raised when a stage has exhausted all retry attempts."""


def register_hook(hook_point: str, hook_fn: Callable) -> None:
    """Register a hook function for a given hook point.

    *hook_point* must be one of: ``pre_create_agent``, ``post_agent_finish``,
    ``pre_root_finish``.
    """
    if hook_point not in _HOOKS:
        raise ValueError(f"Unknown hook point: {hook_point}")
    with _hooks_lock:
        _HOOKS[hook_point].append(hook_fn)


def clear_hooks() -> None:
    """Remove all registered hooks. Useful for testing."""
    with _hooks_lock:
        for point in _HOOKS:
            _HOOKS[point].clear()


def run_pre_create_agent(
    agent_name: str,
    workspace_root: Path | None = None,
) -> StageValidationResult:
    """Execute pre-create_agent hooks for the given stage name.

    Returns a validation result. If ``passed=False``, the caller should
    NOT create the agent and instead return the error to the LLM.
    """
    if workspace_root is None:
        ws = get_current_workspace_root()
        if ws is None:
            return StageValidationResult(
                passed=False,
                stage_name=agent_name,
                check_type="input",
                errors=["No workspace root configured"],
            )
        workspace_root = Path(ws)

    # Look up contract by agent name → stage name mapping
    contract = _resolve_contract(agent_name)
    if contract is not None:
        result = contract.check_input(workspace_root)
        if not result.passed:
            return result
        # Mark stage as in_progress
        sm = get_stage_state_manager()
        sm.transition(contract.stage_name, StageStatus.IN_PROGRESS, "input validation passed")

    # Run any additional registered hooks
    with _hooks_lock:
        for hook in _HOOKS["pre_create_agent"]:
            try:
                hook_result = hook(agent_name=agent_name, workspace_root=workspace_root)
                if isinstance(hook_result, StageValidationResult) and not hook_result.passed:
                    return hook_result
            except Exception as exc:
                return StageValidationResult(
                    passed=False,
                    stage_name=agent_name,
                    check_type="input",
                    errors=[f"Hook error: {exc}"],
                )

    return StageValidationResult(passed=True, stage_name=agent_name, check_type="input")


def run_post_agent_finish(
    agent_name: str,
    agent_id: str,
    findings: list[dict[str, Any]],
    max_retries: int = 3,
    workspace_root: Path | None = None,
) -> StageValidationResult:
    """Execute post-agent_finish hooks (output schema validation).

    On failure with retries remaining: returns a failed result but the
    agent should NOT be marked finished — it continues its loop.
    On failure with retries exhausted: returns a failed result with a
    ``RetryExhaustedError`` flag. The agent is marked as failed.
    """
    if workspace_root is None:
        ws = get_current_workspace_root()
        workspace_root = Path(ws) if ws else None

    contract = _resolve_contract(agent_name)
    sm = get_stage_state_manager()

    if contract is not None:
        sm.transition(contract.stage_name, StageStatus.VALIDATING, "output validation started")
        result = contract.check_output(findings)

        if result.passed:
            sm.transition(contract.stage_name, StageStatus.COMPLETED, "output validation passed")
            _persist_stage_result(contract.stage_name, findings, workspace_root)
            return result

        # Validation failed — handle retry
        stage = sm.get_or_create(contract.stage_name)
        retry_count = stage.increment_retry(agent_id)

        if retry_count <= max_retries:
            sm.transition(
                contract.stage_name,
                StageStatus.IN_PROGRESS,
                f"output validation failed (retry {retry_count}/{max_retries})",
            )
            result.errors.insert(0, f"Retry {retry_count}/{max_retries}:")
            return result
        else:
            sm.transition(
                contract.stage_name,
                StageStatus.FAILED,
                f"retries exhausted ({max_retries})",
            )
            result.errors.insert(0, f"ALL RETRIES EXHAUSTED ({max_retries}):")
            return result

    # Run additional registered hooks
    with _hooks_lock:
        for hook in _HOOKS["post_agent_finish"]:
            try:
                hook(agent_name=agent_name, agent_id=agent_id, findings=findings)
            except Exception:
                pass  # hooks must not crash the agent

    return StageValidationResult(passed=True, stage_name=agent_name, check_type="output")


def run_pre_root_finish(findings: list[dict[str, Any]]) -> StageValidationResult:
    """Validate root agent final deliverables."""
    sm = get_stage_state_manager()
    if not sm.all_completed():
        stages = sm.to_dict()
        incomplete = [n for n, s in stages.items() if s["status"] not in ("completed",)]
        return StageValidationResult(
            passed=False,
            stage_name="root",
            check_type="output",
            errors=[f"Not all stages completed. Incomplete: {', '.join(incomplete)}"],
        )

    with _hooks_lock:
        for hook in _HOOKS["pre_root_finish"]:
            try:
                hook(findings=findings)
            except Exception:
                pass

    return StageValidationResult(passed=True, stage_name="root", check_type="output")


def _resolve_contract(agent_name: str) -> StageContract | None:
    """Map agent names to stage contracts.

    The LLM uses descriptive names like ``FileClassifier``, ``StaticScanner``,
    etc.  We match them to stage contract keys.
    """
    name_to_stage = {
        "fileclassifier": "classify",
        "classifyagent": "classify",
        "staticscanner": "scan",
        "scanagent": "scan",
        "perfileanalyzer": "analyze",
        "analyzeagent": "analyze",
        "crosslinkanalyzer": "cross-link",
        "crosslinkagent": "cross-link",
        "reviewrefiner": "review",
        "reviewagent": "review",
    }
    stage_key = name_to_stage.get(agent_name.lower().replace(" ", "").replace("_", "").replace("-", ""))
    if stage_key:
        return CONTRACTS.get(stage_key)
    # Also try direct match against CONTRACTS keys
    return CONTRACTS.get(agent_name.lower())


def _persist_stage_result(
    stage_name: str,
    findings: list[dict[str, Any]],
    workspace_root: Path | None,
) -> None:
    """Save validated stage output to a JSON file for downstream pre-hooks."""
    if workspace_root is None:
        return
    logs_dir = workspace_root / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    output_path = logs_dir / f"stage_{stage_name}_result.json"
    try:
        output_path.write_text(
            json.dumps({"stage": stage_name, "findings": findings}, indent=2, ensure_ascii=False)
        )
        sm = get_stage_state_manager()
        sm.add_artifact(stage_name, str(output_path))
    except OSError:
        pass  # persistence failure should not crash validation
```

- [ ] **Step 2: Verify import**

Run: `cd /Users/chenhaoran/workspace/agent/nano-strix && .venv/bin/python -c "from nano_strix.agents.deep_analysis_lib.hooks import register_hook, clear_hooks, run_pre_create_agent; print('OK')"`
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add src/nano_strix/agents/deep_analysis_lib/hooks.py
git commit -m "feat: add HarnessHooks with pre/post hook execution and retry management

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 4: Modify graph.py — add _HOOKS registry and pre-hook in create_agent

**Files:**
- Modify: `src/nano_strix/agents/deep_analysis_lib/graph.py`

- [ ] **Step 1: Read current create_agent function around lines 183-323 to confirm context**

Already read — the function starts at line 183 with the `@register_tool` decorator and the main logic is lines 191-323.

- [ ] **Step 2: Add hooks import at top of graph.py**

At line 10 (after the existing imports), add:

```python
from nano_strix.agents.deep_analysis_lib.hooks import (
    run_pre_create_agent,
    run_post_agent_finish,
    run_pre_root_finish,
    register_hook as _register_harness_hook,
    clear_hooks as _clear_harness_hooks,
    RetryExhaustedError,
)
```

Edit the file — after the line `from nano_strix.tools.registry import register_tool`, insert the new import block.

- [ ] **Step 3: Add HOOKS-related module-level exports**

After the existing `_graph_logger` / `_llm_logger` / `_tool_logger` section (around line 167), add:

```python
# Harness hook registration (re-exported from hooks.py)
register_harness_hook = _register_harness_hook
clear_harness_hooks = _clear_harness_hooks
```

- [ ] **Step 4: Insert pre-hook call in create_agent function**

In `create_agent()`, after line 191 (`agent_state = _resolve_agent_state(agent_state)`) but before the `try:` block starting at line 192, add:

```python
        # --- harness pre-hook: validate stage input before creating agent ---
        from nano_strix.tools.context import get_current_workspace_root
        ws_root = get_current_workspace_root()
        workspace_path = Path(ws_root) if ws_root else None
        pre_result = run_pre_create_agent(
            agent_name=name,
            workspace_root=workspace_path,
        )
        if not pre_result.passed:
            return {
                "success": False,
                "error": pre_result.to_message(),
                "agent_id": None,
            }
        # --- end harness pre-hook ---
```

Note: `Path` is already imported at the top of `deep_analysis.py` but not in `graph.py`. Need to add `from pathlib import Path` to graph.py imports (or use it inline since it's only used once — actually, I'll check what's already imported).

Looking at the current imports: graph.py doesn't import `Path` directly. But `context.resolve_and_validate_path` returns a `Path`. So I can just use `Path` from the `contracts.py` import or use `pathlib.Path` directly. Let me add `from pathlib import Path` to graph.py's imports (line 6 area). Actually wait, `resolve_and_validate_path` is already used in context. Let me just add `Path` to the import section.

Actually, looking at the code, I should use `pathlib.Path` to construct the Path from the string. Let me add `from pathlib import Path` at the top of graph.py.

- [ ] **Step 5: Add Path to graph.py imports**

At line 6 (or wherever the typing imports end), ensure `from pathlib import Path` is present.

- [ ] **Step 6: Verify the changes parse correctly**

Run: `cd /Users/chenhaoran/workspace/agent/nano-strix && .venv/bin/python -c "from nano_strix.agents.deep_analysis_lib.graph import create_agent, register_harness_hook; print('OK')"`
Expected: `OK`

- [ ] **Step 7: Commit**

```bash
git add src/nano_strix/agents/deep_analysis_lib/graph.py
git commit -m "feat: add pre-hook in create_agent for stage input validation

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 5: Modify graph.py — add post-hook in agent_finish with retry logic

**Files:**
- Modify: `src/nano_strix/agents/deep_analysis_lib/graph.py`

- [ ] **Step 1: Insert post-hook call in agent_finish function**

In `agent_finish()`, after line 462 (`agent_state = _resolve_agent_state(agent_state)`) and after line 480 (`findings = findings or []`), but BEFORE line 483 (`agent_node["status"] = "finished" if success else "failed"`), add:

```python
        # --- harness post-hook: validate output before marking finished ---
        agent_name = agent_node.get("name", agent_state.agent_name)
        from nano_strix.tools.context import get_current_workspace_root
        ws_root = get_current_workspace_root()
        workspace_path = Path(ws_root) if ws_root else None

        post_result = run_post_agent_finish(
            agent_name=agent_name,
            agent_id=agent_id,
            findings=findings,
            max_retries=3,
            workspace_root=workspace_path,
        )
        if not post_result.passed:
            # Check if retries are exhausted by looking for the signal phrase
            if "ALL RETRIES EXHAUSTED" in post_result.to_message():
                # Mark as failed and report to parent
                if _graph_logger:
                    _graph_logger.log_agent_finished(
                        agent_id=agent_id,
                        success=False,
                        findings_count=len(findings),
                        result_summary=result_summary,
                        validation_result="retry_exhausted",
                        schema_errors=post_result.errors,
                    )
                agent_node["status"] = "failed"
                agent_node["finished_at"] = _now_iso()
                agent_node["result"] = {
                    "summary": result_summary,
                    "findings": findings,
                    "success": False,
                    "recommendations": final_recommendations or [],
                    "harness_error": post_result.to_message(),
                }
                agent_state.final_result = agent_node["result"]
                if report_to_parent and agent_node.get("parent_id"):
                    _notify_parent_of_completion(
                        agent_node, agent_id, findings, final_recommendations or [], False
                    )
                _running_agents.pop(agent_id, None)
                return {
                    "agent_completed": False,
                    "parent_notified": report_to_parent,
                    "completion_summary": {
                        "agent_id": agent_id,
                        "agent_name": agent_node["name"],
                        "task": agent_node["task"],
                        "success": False,
                        "findings_count": len(findings),
                        "harness_error": post_result.to_message(),
                    },
                }
            else:
                # Retries remaining — return error so agent can fix
                if _graph_logger:
                    _graph_logger.log_agent_status_change(
                        agent_id=agent_id,
                        old_status="running",
                        new_status="validating",
                        reason=post_result.to_message(),
                        stage_name=agent_name,
                        checkpoint_detail="output validation failed, retry pending",
                    )
                return {
                    "agent_completed": False,
                    "error": post_result.to_message(),
                    "parent_notified": False,
                }
        # --- end harness post-hook ---
```

This goes between the `findings = findings or []` line and the `agent_node["status"] = "finished"` line — effectively making the post-hook a gate before the agent transitions to finished.

- [ ] **Step 2: Extract parent notification into a helper function**

The parent notification logic is now duplicated (once in the normal flow, once in the retry-exhausted case). Extract it into a helper `_notify_parent_of_completion`:

After the existing helper `_generate_message_id` and `_now_iso` (around line 178), add:

```python
def _notify_parent_of_completion(
    agent_node: dict,
    agent_id: str,
    findings: list,
    recommendations: list,
    success: bool,
) -> None:
    """Send completion report message to parent agent."""
    parent_id = agent_node.get("parent_id")
    if not parent_id or parent_id not in _agent_graph["nodes"]:
        return
    findings_xml = "\n".join(f"        <finding>{f}</finding>" for f in findings)
    recs_xml = "\n".join(f"        <recommendation>{r}</recommendation>" for r in recommendations)
    report_message = f"""<agent_completion_report>
    <agent_info>
        <agent_name>{agent_node["name"]}</agent_name>
        <agent_id>{agent_id}</agent_id>
        <task>{agent_node["task"]}</task>
        <status>{"SUCCESS" if success else "FAILED"}</status>
        <completion_time>{agent_node.get("finished_at", _now_iso())}</completion_time>
    </agent_info>
    <results>
        <summary>{agent_node.get("result", {}).get("summary", "")}</summary>
        <findings>
{findings_xml}
        </findings>
        <recommendations>
{recs_xml}
        </recommendations>
    </results>
</agent_completion_report>"""
    if parent_id not in _agent_messages:
        _agent_messages[parent_id] = []
    _agent_messages[parent_id].append({
        "id": f"report_{uuid.uuid4().hex[:8]}",
        "from": agent_id,
        "to": parent_id,
        "content": report_message,
        "message_type": "information",
        "priority": "high",
        "timestamp": _now_iso(),
        "delivered": True,
        "read": False,
    })
    parent_state = _agent_states.get(parent_id)
    if parent_state is not None and parent_state.waiting_for_input:
        parent_state.signal_wake()
```

- [ ] **Step 3: Replace the inline parent notification in agent_finish with the helper call**

In the existing `agent_finish`, replace the parent notification block (lines 504-553) with a single call:

```python
        if report_to_parent and agent_node.get("parent_id"):
            _notify_parent_of_completion(
                agent_node, agent_id, findings, final_recommendations or [], success
            )
            parent_notified = True
```

- [ ] **Step 4: Verify the changes parse correctly**

Run: `cd /Users/chenhaoran/workspace/agent/nano-strix && .venv/bin/python -c "from nano_strix.agents.deep_analysis_lib.graph import agent_finish, _notify_parent_of_completion; print('OK')"`
Expected: `OK`

- [ ] **Step 5: Commit**

```bash
git add src/nano_strix/agents/deep_analysis_lib/graph.py
git commit -m "feat: add post-hook in agent_finish with retry gating

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 6: Modify graph.py — enhance view_agent_graph with stage states

**Files:**
- Modify: `src/nano_strix/agents/deep_analysis_lib/graph.py`

- [ ] **Step 1: In view_agent_graph, add stage state info to the summary**

In `view_agent_graph()` (around line 619-638), after the `summary` dict and before the return, add stage state info:

```python
        # --- harness: include stage progress ---
        from nano_strix.agents.deep_analysis_lib.stage_state import get_stage_state_manager
        sm = get_stage_state_manager()
        stage_info = sm.to_dict()
        if stage_info:
            lines.append("")
            lines.append("=== STAGE PROGRESS ===")
            for sname, sdata in stage_info.items():
                status_icon = {
                    "completed": "DONE",
                    "failed": "FAIL",
                    "in_progress": "RUNNING",
                    "validating": "VALIDATE",
                    "pending": "PENDING",
                }.get(sdata["status"], sdata["status"])
                lines.append(
                    f"  [{status_icon}] {sname} | agents={sdata['agent_count']} "
                    f"| retries={sdata['retry_counts']} | last={sdata['last_checkpoint'][:60]}"
                )
        summary["stages"] = stage_info
        # --- end harness ---
```

- [ ] **Step 2: Verify import**

Run: `cd /Users/chenhaoran/workspace/agent/nano-strix && .venv/bin/python -c "from nano_strix.agents.deep_analysis_lib.graph import view_agent_graph; print('OK')"`
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add src/nano_strix/agents/deep_analysis_lib/graph.py
git commit -m "feat: add stage progress to view_agent_graph output

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 7: Modify graph_logger.py — add harness fields

**Files:**
- Modify: `src/nano_strix/logging/graph_logger.py`

- [ ] **Step 1: Update log_agent_status_change to accept optional harness fields**

Change the method signature and write call:

```python
    def log_agent_status_change(
        self,
        agent_id: str,
        old_status: str,
        new_status: str,
        reason: str = "",
        stage_name: str = "",
        checkpoint_detail: str = "",
    ) -> None:
        self._write(
            "agent_status_change",
            {
                "agent_id": agent_id,
                "old_status": old_status,
                "new_status": new_status,
                "reason": reason,
                "stage_name": stage_name,
                "checkpoint_detail": checkpoint_detail,
            },
        )
```

- [ ] **Step 2: Update log_agent_finished to accept optional harness fields**

Change the method signature and write call:

```python
    def log_agent_finished(
        self,
        agent_id: str,
        success: bool,
        findings_count: int,
        result_summary: str,
        validation_result: str = "",
        schema_errors: list[str] | None = None,
    ) -> None:
        self._write(
            "agent_finished",
            {
                "agent_id": agent_id,
                "success": success,
                "findings_count": findings_count,
                "result_summary": result_summary,
                "validation_result": validation_result,
                "schema_errors": schema_errors or [],
            },
        )
```

- [ ] **Step 3: Verify — existing tests still pass (no behavior change for old callers)**

Run: `cd /Users/chenhaoran/workspace/agent/nano-strix && .venv/bin/pytest tests/test_deep_analysis_logging.py -v`
Expected: All tests pass (old callers don't pass the new kwargs, which default to empty)

- [ ] **Step 4: Commit**

```bash
git add src/nano_strix/logging/graph_logger.py
git commit -m "feat: add harness fields to GraphLogger events

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 8: Modify deep_analysis.py — initialize harness at entry point

**Files:**
- Modify: `src/nano_strix/agents/deep_analysis.py`

- [ ] **Step 1: Add harness imports**

After the existing imports (around line 35), add:

```python
from nano_strix.agents.deep_analysis_lib.hooks import register_hook, clear_hooks
from nano_strix.agents.deep_analysis_lib.stage_state import (
    get_stage_state_manager,
    reset_stage_state_manager,
)
```

- [ ] **Step 2: Add harness initialization in main()**

After the graph_logger is set (line 120: `set_tool_logger(tool_logger)`), add harness initialization and a cleanup registration:

```python
    # Initialize harness — register default hooks for stage contracts
    from nano_strix.agents.deep_analysis_lib.hooks import register_hook
    # Hooks are auto-invoked via graph.py's create_agent/agent_finish;
    # the contracts themselves handle the default validation logic.
    # No additional hooks need registration — the pre/post hook functions
    # in hooks.py are called directly from graph.py.
    #
    # Reset any stale stage state from previous runs
    reset_stage_state_manager()
    sm = get_stage_state_manager()
    logger.debug("Harness initialized: %d contracts loaded", 5)
```

- [ ] **Step 3: Verify full import chain**

Run: `cd /Users/chenhaoran/workspace/agent/nano-strix && .venv/bin/python -c "from nano_strix.agents.deep_analysis import main; print('OK')"`
Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add src/nano_strix/agents/deep_analysis.py
git commit -m "feat: initialize harness and stage state manager in deep_analysis entry

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 9: Unit tests — test_harness_contracts.py

**Files:**
- Create: `tests/test_harness_contracts.py`

- [ ] **Step 1: Write the test file**

```python
"""Unit tests for stage contracts and schema validator."""

from __future__ import annotations

import json
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
        result = contract.check_output({"not": "a list"})  # type: ignore
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

        invalid = [{"related_findings": ["F-001"]}]  # missing relation_type, combined_severity
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
```

- [ ] **Step 2: Run the test file**

Run: `cd /Users/chenhaoran/workspace/agent/nano-strix && .venv/bin/pytest tests/test_harness_contracts.py -v`
Expected: `~22 passed`

- [ ] **Step 3: Commit**

```bash
git add tests/test_harness_contracts.py
git commit -m "test: add unit tests for StageContract and schema validator

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 10: Integration tests — test_harness_hooks.py

**Files:**
- Create: `tests/test_harness_hooks.py`

- [ ] **Step 1: Write the integration test file**

```python
"""Integration tests for harness hooks with graph tools."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from nano_strix.agents.deep_analysis_lib.contracts import StageValidationResult
from nano_strix.agents.deep_analysis_lib.hooks import (
    RetryExhaustedError,
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
        (tmp_path / "file_manifest.json").write_text('{"files": {"a.py": {"status": "classified"}}}')
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
        (tmp_path / "file_manifest.json").write_text('{"files": {"a.py": {"status": "classified"}}}')
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
            return StageValidationResult(passed=True, stage_name=agent_name, check_type="input")

        register_hook("pre_create_agent", my_hook)
        run_pre_create_agent("TestAgent", workspace_root=tmp_path)
        assert "TestAgent" in called


class TestPostAgentFinishHook:
    def test_passes_with_valid_findings(self, tmp_path: Path, monkeypatch):
        monkeypatch.setattr(
            "nano_strix.agents.deep_analysis_lib.hooks.get_current_workspace_root",
            lambda: str(tmp_path),
        )
        findings = [{
            "file_path": "a.py",
            "vulnerability_type": "xss",
            "severity": "high",
            "location": "line 10",
            "description": "desc",
        }]
        result = run_post_agent_finish(
            agent_name="StaticScanner",
            agent_id="agent_test_1",
            findings=findings,
            workspace_root=tmp_path,
        )
        assert result.passed
        # Stage should be completed
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
        # Exhaust all retries
        for _ in range(4):  # max_retries=3 (default) → 4th call exhausts
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
        findings = [{
            "file_path": "a.py",
            "vulnerability_type": "xss",
            "severity": "high",
            "location": "line 10",
            "description": "desc",
        }]
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
```

- [ ] **Step 2: Run the integration test file**

Run: `cd /Users/chenhaoran/workspace/agent/nano-strix && .venv/bin/pytest tests/test_harness_hooks.py -v`
Expected: `~12 passed`

- [ ] **Step 3: Commit**

```bash
git add tests/test_harness_hooks.py
git commit -m "test: add integration tests for harness hooks and stage state lifecycle

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 11: Update existing integration test for harness compatibility

**Files:**
- Modify: `tests/test_deep_analysis_stage_integration.py`

- [ ] **Step 1: Add harness cleanup to the existing fixture**

In `_reset_graph_globals` (around line 156), add harness cleanup alongside the existing graph cleanup:

```python
@pytest.fixture(autouse=True)
def _reset_graph_globals():
    _cleanup_globals()
    # Also reset harness state
    from nano_strix.agents.deep_analysis_lib.hooks import clear_hooks
    from nano_strix.agents.deep_analysis_lib.stage_state import reset_stage_state_manager
    clear_hooks()
    reset_stage_state_manager()
    yield
    _cleanup_globals()
    clear_hooks()
    reset_stage_state_manager()
```

- [ ] **Step 2: In the test_full_five_phase_orchestration test, register a no-op hook so the harness is active but permissive**

After the monkeypatch section (around line 483), add:

```python
    # Register a permissive harness hook that passes all validations.
    # This ensures harness code is exercised but doesn't block the scripted flow.
    from nano_strix.agents.deep_analysis_lib.hooks import register_hook
    from nano_strix.agents.deep_analysis_lib.contracts import StageValidationResult

    def _permissive_pre_hook(agent_name, workspace_root, **kwargs):
        return StageValidationResult(passed=True, stage_name=agent_name, check_type="input")

    def _permissive_post_hook(agent_name, agent_id, findings, **kwargs):
        return StageValidationResult(passed=True, stage_name=agent_name, check_type="output")

    register_hook("pre_create_agent", _permissive_pre_hook)
    register_hook("post_agent_finish", _permissive_post_hook)
```

Wait — actually, the pre-hook and post-hook are called directly from `create_agent` and `agent_finish` via `run_pre_create_agent` and `run_post_agent_finish`. These functions also run the contract checks. For the integration test to work, the workspace must satisfy the contract conditions.

The test creates files in `tmp_path` (not necessarily with `file_manifest.json`), so the contract-based pre-hooks would block the `StaticScanner` creation (it needs `file_manifest.json`).

The simplest fix: ensure the test workspace has the required files for each stage. Let me add a step that creates the necessary stage artifacts before the test runs.

Actually, the simpler approach: in the integration test, create the needed workspace files so the contracts pass, OR make the test bypass the harness entirely.

The cleanest approach: create the required stage artifacts in the test setup. Let me add that to the test.

- [ ] **Step 2 (revised): Create required stage artifacts in the test setup**

After the monkeypatch block (around line 483), add creation of stage result files so contract input predicates pass:

```python
    # Create stage result artifacts that harness contracts expect
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir(exist_ok=True)
    (tmp_path / "file_manifest.json").write_text(
        '{"files": {"main.py": {"status": "classified"}, '
        '"services/crypto_service.py": {"status": "classified"}}}'
    )
    for stage in ("scan", "analyze", "cross-link"):
        (logs_dir / f"stage_{stage}_result.json").write_text('{"stage": "' + stage + '", "findings": []}')
```

- [ ] **Step 3: Run the existing integration tests to verify they still pass**

Run: `cd /Users/chenhaoran/workspace/agent/nano-strix && .venv/bin/pytest tests/test_deep_analysis_stage_integration.py -v`
Expected: `2 passed` (both tests should still pass)

- [ ] **Step 4: Commit**

```bash
git add tests/test_deep_analysis_stage_integration.py
git commit -m "test: update integration tests for harness compatibility

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 12: Final verification — run all tests

**Files:**
- None (verification only)

- [ ] **Step 1: Run the full test suite**

Run: `cd /Users/chenhaoran/workspace/agent/nano-strix && .venv/bin/pytest -v`
Expected: All tests pass (no regressions)

- [ ] **Step 2: Run linter**

Run: `cd /Users/chenhaoran/workspace/agent/nano-strix && .venv/bin/ruff check src/ tests/`
Expected: No errors

- [ ] **Step 3: Run format check**

Run: `cd /Users/chenhaoran/workspace/agent/nano-strix && .venv/bin/ruff format --check src/ tests/`
Expected: No format issues (or auto-fix if any)

- [ ] **Step 4: Commit any lint/format fixes**

```bash
git add -A && git commit -m "chore: apply lint and format fixes

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```
(Only if changes needed)
```

