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

    # Look up contract by agent name -> stage name mapping
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
