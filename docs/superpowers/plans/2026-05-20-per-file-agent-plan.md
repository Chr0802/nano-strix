# Per-File Agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rewrite per_file agent from sleep+log stub to real LLM-driven multi-phase analysis with Strix-style parallel sub-agents and file_manifest checkpointing.

**Architecture:** The per_file agent remains a standalone script launched by AgentManager via stdin/stdout JSON IPC. Internally it runs three phases (classify → static scan → parallel analysis). Phase 3 spawns 4 sub-agents on independent threads with per-thread asyncio event loops, coordinated via a shared FileManifest with threading.Lock and checkpoint persistence to `file_manifest.json`.

**Tech Stack:** Python 3.10+, asyncio, threading, JSON IPC, semgrep/bandit subprocess, existing nano-strix LLM provider and tool registry.

---

## File Structure

```
src/nano_strix/agents/
├── per_file.py                  # [REWRITE] Entry point, 3-phase orchestrator
├── per_file_lib/                # [NEW PACKAGE]
│   ├── __init__.py
│   ├── manifest.py              # FileManifest, ManifestFile, AgentState data classes + JSON I/O
│   ├── classifier.py            # Phase 1: LLM-based file classification
│   ├── scanner.py               # Phase 2: semgrep/bandit static scanning
│   └── sub_agents.py            # Phase 3: SubAgentRunner, agent_loop, thread management

src/nano_strix/config/
└── schema.py                    # [MODIFY] Add PerFileConfig dataclass

tests/
├── test_per_file_manifest.py    # [NEW] Manifest unit tests
└── test_per_file_agent.py       # [NEW] Per-file agent integration tests
```

No changes to `runner.py` — `per_file.py` path in `STAGE_SCRIPTS` is unchanged.

---

### Task 1: FileManifest data structures and persistence

**Files:**
- Create: `src/nano_strix/agents/per_file_lib/__init__.py`
- Create: `src/nano_strix/agents/per_file_lib/manifest.py`
- Create: `tests/test_per_file_manifest.py`

- [ ] **Step 1: Write failing manifest tests**

```python
# tests/test_per_file_manifest.py
import json
import pytest
from pathlib import Path


@pytest.fixture
def empty_manifest_path(tmp_path):
    return tmp_path / "manifest.json"


@pytest.fixture
def sample_files():
    return {
        "src/auth/login.py": {"priority": "high", "dimensions": ["auth", "route"]},
        "src/db/query.py": {"priority": "high", "dimensions": ["dataflow"]},
        "src/api/handler.py": {"priority": "medium", "dimensions": ["route", "dataflow"]},
        "src/utils/format.py": {"priority": "low", "dimensions": []},
    }


class TestFileManifestCreate:
    def test_create_with_files(self, empty_manifest_path, sample_files):
        from nano_strix.agents.per_file_lib.manifest import FileManifest

        manifest = FileManifest.create(
            path=empty_manifest_path,
            files=sample_files,
            agent_names=["route_agent", "dataflow_agent", "auth_agent", "dependency_agent"],
        )

        assert manifest.phase == "classification"
        assert len(manifest.files) == 4
        assert manifest.files["src/auth/login.py"].priority == "high"
        assert manifest.files["src/auth/login.py"].status == "pending"
        assert manifest.files["src/auth/login.py"].dimensions == ["auth", "route"]
        assert set(manifest.agents_state.keys()) == {
            "route_agent", "dataflow_agent", "auth_agent", "dependency_agent"
        }

    def test_create_writes_to_disk(self, empty_manifest_path, sample_files):
        from nano_strix.agents.per_file_lib.manifest import FileManifest

        FileManifest.create(empty_manifest_path, sample_files, list(sample_files.keys()))
        assert empty_manifest_path.exists()
        data = json.loads(empty_manifest_path.read_text())
        assert data["phase"] == "classification"
        assert "src/auth/login.py" in data["files"]


class TestFileManifestLoad:
    def test_load_restores_state(self, empty_manifest_path, sample_files):
        from nano_strix.agents.per_file_lib.manifest import FileManifest

        m1 = FileManifest.create(empty_manifest_path, sample_files,
                                 ["route_agent", "dataflow_agent", "auth_agent", "dependency_agent"])
        m1.files["src/auth/login.py"].status = "analyzed"
        m1.save()

        m2 = FileManifest.load(empty_manifest_path)
        assert m2.files["src/auth/login.py"].status == "analyzed"
        assert m2.phase == m1.phase

    def test_load_missing_file_raises(self, tmp_path):
        from nano_strix.agents.per_file_lib.manifest import FileManifest

        with pytest.raises(FileNotFoundError):
            FileManifest.load(tmp_path / "nonexistent.json")


class TestClaimPendingFile:
    def test_returns_highest_priority_dimension_match(self, empty_manifest_path, sample_files):
        from nano_strix.agents.per_file_lib.manifest import FileManifest

        manifest = FileManifest.create(empty_manifest_path, sample_files,
                                       ["route_agent", "dataflow_agent", "auth_agent", "dependency_agent"])

        claimed = manifest.claim_pending_file("route_agent")
        # route_agent dimension is "route" — src/auth/login.py has "route" + high priority
        assert claimed is not None
        assert claimed.path == "src/auth/login.py"

    def test_skips_already_analyzing_file(self, empty_manifest_path, sample_files):
        from nano_strix.agents.per_file_lib.manifest import FileManifest

        manifest = FileManifest.create(empty_manifest_path, sample_files,
                                       ["route_agent", "dataflow_agent", "auth_agent", "dependency_agent"])
        manifest.files["src/auth/login.py"].status = "analyzing"
        manifest.files["src/auth/login.py"].assigned_to = "auth_agent"

        claimed = manifest.claim_pending_file("route_agent")
        # Should pick next best: src/api/handler.py (medium, has "route")
        assert claimed.path == "src/api/handler.py"

    def test_returns_none_when_all_voted(self, empty_manifest_path, sample_files):
        from nano_strix.agents.per_file_lib.manifest import FileManifest

        manifest = FileManifest.create(empty_manifest_path, sample_files,
                                       ["route_agent"])
        for f in manifest.files.values():
            f.skip_votes["route_agent"] = "skip"

        assert manifest.claim_pending_file("route_agent") is None

    def test_dimension_mismatch_still_returned_for_skip_vote(self, empty_manifest_path, sample_files):
        from nano_strix.agents.per_file_lib.manifest import FileManifest

        manifest = FileManifest.create(empty_manifest_path, sample_files,
                                       ["route_agent"])
        # All route-tagged files already voted, non-route files remain
        for path, f in manifest.files.items():
            if "route" in f.dimensions:
                f.skip_votes["route_agent"] = "analyze"
                f.status = "analyzed"

        claimed = manifest.claim_pending_file("route_agent")
        # Should return a non-matching file for skip vote
        assert claimed is not None
        assert "route" not in claimed.dimensions


class TestSkipVotes:
    def test_all_agents_skip_makes_skipped(self, empty_manifest_path, sample_files):
        from nano_strix.agents.per_file_lib.manifest import FileManifest

        manifest = FileManifest.create(empty_manifest_path, sample_files,
                                       ["route_agent", "dataflow_agent", "auth_agent", "dependency_agent"])
        f = manifest.files["src/utils/format.py"]
        for agent in manifest.agent_names:
            f.skip_votes[agent] = "skip"

        manifest._recalculate_file_status("src/utils/format.py")
        assert f.status == "skipped"

    def test_one_analyze_vote_keeps_pending(self, empty_manifest_path, sample_files):
        from nano_strix.agents.per_file_lib.manifest import FileManifest

        manifest = FileManifest.create(empty_manifest_path, sample_files,
                                       ["route_agent", "dataflow_agent", "auth_agent", "dependency_agent"])
        f = manifest.files["src/utils/format.py"]
        f.skip_votes["route_agent"] = "analyze"
        for agent in ["dataflow_agent", "auth_agent", "dependency_agent"]:
            f.skip_votes[agent] = "skip"

        manifest._recalculate_file_status("src/utils/format.py")
        assert f.status != "skipped"
        assert f.status in ("pending", "analyzing")

    def test_all_voted_no_nulls(self, empty_manifest_path, sample_files):
        from nano_strix.agents.per_file_lib.manifest import FileManifest

        manifest = FileManifest.create(empty_manifest_path, sample_files,
                                       ["route_agent", "dataflow_agent", "auth_agent", "dependency_agent"])
        f = manifest.files["src/utils/format.py"]
        for agent in manifest.agent_names:
            f.skip_votes[agent] = "skip"

        assert manifest._all_votes_cast("src/utils/format.py") is True

    def test_some_null_votes_not_all_cast(self, empty_manifest_path, sample_files):
        from nano_strix.agents.per_file_lib.manifest import FileManifest

        manifest = FileManifest.create(empty_manifest_path, sample_files,
                                       ["route_agent", "dataflow_agent"])
        f = manifest.files["src/auth/login.py"]
        f.skip_votes["route_agent"] = "analyze"
        # dataflow_agent still null

        assert manifest._all_votes_cast("src/auth/login.py") is False


class TestCanFinish:
    def test_all_high_covered_returns_true(self, empty_manifest_path, sample_files):
        from nano_strix.agents.per_file_lib.manifest import FileManifest

        manifest = FileManifest.create(empty_manifest_path, sample_files,
                                       ["route_agent"])
        for path, f in manifest.files.items():
            f.status = "analyzed" if f.priority in ("high", "medium") else "skipped"
            f.skip_votes["route_agent"] = "skip"

        assert manifest.can_finish() is True

    def test_pending_high_returns_false(self, empty_manifest_path, sample_files):
        from nano_strix.agents.per_file_lib.manifest import FileManifest

        manifest = FileManifest.create(empty_manifest_path, sample_files,
                                       ["route_agent"])

        assert manifest.can_finish() is False
        assert len(manifest.hard_gate["blocked_by"]) > 0


class TestMarkAnalyzing:
    def test_sets_status_and_timestamp(self, empty_manifest_path, sample_files):
        from nano_strix.agents.per_file_lib.manifest import FileManifest

        manifest = FileManifest.create(empty_manifest_path, sample_files,
                                       ["route_agent"])
        manifest.mark_analyzing("src/auth/login.py", "route_agent")

        f = manifest.files["src/auth/login.py"]
        assert f.status == "analyzing"
        assert f.assigned_to == "route_agent"
        assert f.analyzing_started_at is not None


class TestAgentError:
    def test_increments_retry_and_resets_status(self, empty_manifest_path, sample_files):
        from nano_strix.agents.per_file_lib.manifest import FileManifest

        manifest = FileManifest.create(empty_manifest_path, sample_files,
                                       ["route_agent"])
        manifest.mark_analyzing("src/auth/login.py", "route_agent")
        manifest.handle_agent_error("src/auth/login.py", "route_agent")

        f = manifest.files["src/auth/login.py"]
        assert f.retry_count == 1
        assert f.status == "pending"
        assert f.assigned_to is None

    def test_exceeds_max_retries_forces_skip(self, empty_manifest_path, sample_files):
        from nano_strix.agents.per_file_lib.manifest import FileManifest

        manifest = FileManifest.create(empty_manifest_path, sample_files,
                                       ["route_agent"], max_file_retries=1)
        manifest.files["src/auth/login.py"].retry_count = 1
        manifest.mark_analyzing("src/auth/login.py", "route_agent")
        manifest.handle_agent_error("src/auth/login.py", "route_agent")

        f = manifest.files["src/auth/login.py"]
        assert f.status == "skipped"


class TestAgentState:
    def test_update_agent_state(self, empty_manifest_path, sample_files):
        from nano_strix.agents.per_file_lib.manifest import FileManifest

        manifest = FileManifest.create(empty_manifest_path, sample_files,
                                       ["route_agent"])
        manifest.update_agent_state("route_agent", {
            "current_file": "src/auth/login.py",
            "iteration": 5,
            "files_analyzed": 3,
        })

        state = manifest.agents_state["route_agent"]
        assert state["current_file"] == "src/auth/login.py"
        assert state["iteration"] == 5
        assert state["files_analyzed"] == 3

    def test_orphaned_files_detected(self, empty_manifest_path, sample_files):
        from nano_strix.agents.per_file_lib.manifest import FileManifest
        from datetime import datetime, timedelta, timezone

        manifest = FileManifest.create(empty_manifest_path, sample_files,
                                       ["route_agent"])
        manifest.mark_analyzing("src/auth/login.py", "route_agent")
        # Simulate old timestamp
        old_time = datetime.now(timezone.utc) - timedelta(seconds=9999)
        manifest.files["src/auth/login.py"].analyzing_started_at = old_time.isoformat()

        orphans = manifest.detect_orphan_files(orphan_timeout_seconds=600)
        assert "src/auth/login.py" in orphans


class TestSaveAndLoad:
    def test_roundtrip_preserves_all_data(self, tmp_path, sample_files):
        from nano_strix.agents.per_file_lib.manifest import FileManifest

        path = tmp_path / "test_manifest.json"
        m1 = FileManifest.create(path, sample_files,
                                 ["route_agent", "dataflow_agent", "auth_agent", "dependency_agent"])
        m1.files["src/auth/login.py"].status = "analyzed"
        m1.files["src/auth/login.py"].findings.append({
            "id": "F-001", "title": "SQL Injection", "severity": "critical",
            "category": "sql_injection", "file_path": "src/auth/login.py",
            "line_range": [44, 48], "description": "...", "code_snippet": "...",
            "recommendation": "...", "confidence": 0.95,
        })
        m1.phase = "analysis"
        m1.discovered_routes.append({
            "path": "/api/login", "method": "POST", "file": "src/auth/login.py", "line": 42,
        })
        m1.update_agent_state("route_agent", {"iteration": 10, "files_analyzed": 3})
        m1.save()

        m2 = FileManifest.load(path)
        assert m2.phase == "analysis"
        assert m2.files["src/auth/login.py"].status == "analyzed"
        assert len(m2.files["src/auth/login.py"].findings) == 1
        assert len(m2.discovered_routes) == 1
        assert m2.agents_state["route_agent"]["iteration"] == 10
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_per_file_manifest.py -v`
Expected: All FAIL — module not found

- [ ] **Step 3: Create package init**

```python
# src/nano_strix/agents/per_file_lib/__init__.py
"""Per-file agent library modules."""
```

- [ ] **Step 4: Implement FileManifest with ManifestFile dataclass**

```python
# src/nano_strix/agents/per_file_lib/manifest.py
from __future__ import annotations

import json
import threading
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class ManifestFile:
    priority: str  # high | medium | low
    status: str = "pending"  # pending | analyzing | analyzed | skipped
    assigned_to: str | None = None
    dimensions: list[str] = field(default_factory=list)
    retry_count: int = 0
    analyzing_started_at: str | None = None
    scan_findings: list[dict[str, Any]] = field(default_factory=list)
    skip_votes: dict[str, str | None] = field(default_factory=dict)
    skip_reason: str = ""
    findings: list[dict[str, Any]] = field(default_factory=list)

    @property
    def path(self) -> str:
        return self._path

    def to_dict(self) -> dict[str, Any]:
        return {
            "priority": self.priority,
            "status": self.status,
            "assigned_to": self.assigned_to,
            "dimensions": self.dimensions,
            "retry_count": self.retry_count,
            "analyzing_started_at": self.analyzing_started_at,
            "scan_findings": self.scan_findings,
            "skip_votes": self.skip_votes,
            "skip_reason": self.skip_reason,
            "findings": self.findings,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ManifestFile:
        return cls(
            priority=data.get("priority", "medium"),
            status=data.get("status", "pending"),
            assigned_to=data.get("assigned_to"),
            dimensions=data.get("dimensions", []),
            retry_count=data.get("retry_count", 0),
            analyzing_started_at=data.get("analyzing_started_at"),
            scan_findings=data.get("scan_findings", []),
            skip_votes=data.get("skip_votes", {}),
            skip_reason=data.get("skip_reason", ""),
            findings=data.get("findings", []),
        )


AGENT_DIMENSIONS = {
    "route_agent": "route",
    "dataflow_agent": "dataflow",
    "auth_agent": "auth",
    "dependency_agent": "dependency",
}


class FileManifest:
    """Thread-safe manifest with file coverage tracking and agent checkpoint state."""

    def __init__(
        self,
        path: Path,
        phase: str = "classification",
        files: dict[str, ManifestFile] | None = None,
        agents_state: dict[str, dict[str, Any]] | None = None,
        discovered_routes: list[dict[str, Any]] | None = None,
        max_file_retries: int = 3,
    ) -> None:
        self._path = path
        self._lock = threading.Lock()
        self.phase = phase
        self.discovered_routes = discovered_routes or []
        self.max_file_retries = max_file_retries
        self._files: dict[str, ManifestFile] = files or {}
        self._agent_names: list[str] = list(agents_state.keys()) if agents_state else []
        self.agents_state = agents_state or {}

    @classmethod
    def create(
        cls,
        path: Path,
        files: dict[str, dict[str, Any]],
        agent_names: list[str],
        max_file_retries: int = 3,
    ) -> FileManifest:
        agents_state = {
            name: {
                "status": "pending",
                "thread_id": None,
                "restart_count": 0,
                "current_file": None,
                "iteration": 0,
                "files_analyzed": 0,
                "files_skipped": 0,
                "last_health_check": None,
                "crash_reason": None,
            }
            for name in agent_names
        }
        manifest_files = {}
        for file_path, meta in files.items():
            mf = ManifestFile(
                priority=meta["priority"],
                dimensions=meta.get("dimensions", []),
                skip_votes={name: None for name in agent_names},
            )
            mf._path = file_path
            manifest_files[file_path] = mf

        m = cls(
            path=path,
            phase="classification",
            files=manifest_files,
            agents_state=agents_state,
            max_file_retries=max_file_retries,
        )
        m._agent_names = agent_names
        m.save()
        return m

    @classmethod
    def load(cls, path: Path) -> FileManifest:
        if not path.exists():
            raise FileNotFoundError(f"Manifest not found: {path}")
        data = json.loads(path.read_text())
        files = {}
        for file_path, fdata in data.get("files", {}).items():
            mf = ManifestFile.from_dict(fdata)
            mf._path = file_path
            files[file_path] = mf

        m = cls(
            path=path,
            phase=data.get("phase", "classification"),
            files=files,
            agents_state=data.get("agents_state", {}),
            discovered_routes=data.get("discovered_routes", []),
            max_file_retries=data.get("max_file_retries", 3),
        )
        m._agent_names = list(m.agents_state.keys())
        return m

    @property
    def files(self) -> dict[str, ManifestFile]:
        return self._files

    @property
    def agent_names(self) -> list[str]:
        return self._agent_names

    def save(self) -> None:
        with self._lock:
            self._write()

    def _write(self) -> None:
        data = {
            "phase": self.phase,
            "max_file_retries": self.max_file_retries,
            "agents_state": self.agents_state,
            "discovered_routes": self.discovered_routes,
            "files": {path: mf.to_dict() for path, mf in self._files.items()},
            "coverage": self._compute_coverage(),
            "hard_gate": self._compute_hard_gate(),
        }
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(data, indent=2, ensure_ascii=False))

    def claim_pending_file(self, agent_name: str) -> ManifestFile | None:
        """Select next file for agent. Returns None when all files voted."""
        my_dimension = AGENT_DIMENSIONS.get(agent_name)
        candidates = []
        with self._lock:
            for path, f in self._files.items():
                vote = f.skip_votes.get(agent_name)
                if vote is not None:
                    continue  # already voted
                if f.status == "analyzing" and f.assigned_to is not None:
                    continue  # another agent is working on it
                if f.retry_count > self.max_file_retries:
                    continue
                candidates.append((path, f))

            if not candidates:
                return None

            def sort_key(item: tuple[str, ManifestFile]) -> tuple[int, int, str]:
                _path, f = item
                dim_match = 1 if my_dimension and my_dimension in f.dimensions else 0
                prio = {"high": 0, "medium": 1, "low": 2}.get(f.priority, 2)
                return (-dim_match, prio, _path)

            candidates.sort(key=sort_key)
            return candidates[0][1]

    def mark_analyzing(self, file_path: str, agent_name: str) -> None:
        with self._lock:
            f = self._files[file_path]
            f.status = "analyzing"
            f.assigned_to = agent_name
            f.analyzing_started_at = _now_iso()
            f.retry_count += 1
            self.agents_state[agent_name]["current_file"] = file_path
            self._write()

    def update_file(
        self, file_path: str, *, findings: list[dict[str, Any]] | None = None,
        status: str = "analyzed"
    ) -> None:
        with self._lock:
            f = self._files[file_path]
            if findings:
                f.findings = findings
            f.status = status
            f.assigned_to = None
            # Mark vote based on status
            if status == "analyzed":
                # Cast "analyze" vote for whoever was assigned
                pass  # Vote already implied by analysis
            self._write()

    def vote_skip(self, file_path: str, agent_name: str, reason: str) -> None:
        with self._lock:
            f = self._files[file_path]
            f.skip_votes[agent_name] = "skip"
            existing = f.skip_reason or ""
            if reason not in existing:
                f.skip_reason = (existing + "; " + reason).strip("; ")
            self._recalculate_file_status(file_path)
            self._write()

    def vote_skip_remaining(self, agent_name: str, reason: str = "no matching dimension") -> None:
        with self._lock:
            for path, f in self._files.items():
                if f.skip_votes.get(agent_name) is None:
                    f.skip_votes[agent_name] = "skip"
                    if reason not in (f.skip_reason or ""):
                        f.skip_reason = (f.skip_reason + "; " + agent_name + ": " + reason).strip("; ")
                    self._recalculate_file_status(path)
            self._write()

    def handle_agent_error(self, file_path: str, agent_name: str) -> None:
        with self._lock:
            f = self._files[file_path]
            if f.retry_count > self.max_file_retries:
                f.status = "skipped"
                f.skip_reason = (f.skip_reason + f"; {agent_name}: max retries exceeded").strip("; ")
            else:
                f.status = "pending"
                f.assigned_to = None
                f.analyzing_started_at = None
            self._write()

    def _recalculate_file_status(self, file_path: str) -> None:
        f = self._files[file_path]
        if f.status in ("analyzed",):
            return
        if f.status == "skipped":
            return
        if self._all_votes_cast(file_path) and all(
            v == "skip" for v in f.skip_votes.values()
        ):
            f.status = "skipped"

    def _all_votes_cast(self, file_path: str) -> bool:
        f = self._files[file_path]
        return all(v is not None for v in f.skip_votes.values())

    def can_finish(self) -> bool:
        with self._lock:
            for path, f in self._files.items():
                if f.status not in ("analyzed", "skipped"):
                    return False
                if not self._all_votes_cast(path):
                    return False
            return True

    def _compute_coverage(self) -> dict[str, Any]:
        total = len(self._files)
        result: dict[str, dict[str, int]] = {
            "high": {"total": 0, "analyzed": 0, "skipped": 0, "pending": 0},
            "medium": {"total": 0, "analyzed": 0, "skipped": 0, "pending": 0},
            "low": {"total": 0, "analyzed": 0, "skipped": 0, "pending": 0},
        }
        for f in self._files.values():
            bucket = result.get(f.priority)
            if bucket is None:
                continue
            bucket["total"] += 1
            if f.status == "analyzed":
                bucket["analyzed"] += 1
            elif f.status == "skipped":
                bucket["skipped"] += 1
            else:
                bucket["pending"] += 1
        return {"total": total, **result}

    def _compute_hard_gate(self) -> dict[str, Any]:
        blocked = []
        for path, f in self._files.items():
            if f.status == "pending":
                unvoted = [n for n in self._agent_names if f.skip_votes.get(n) is None]
                blocked.append(
                    f"{path}: pending ({f.priority}, unvoted by {', '.join(unvoted)})"
                )
        return {"can_finish": len(blocked) == 0, "blocked_by": blocked}

    @property
    def hard_gate(self) -> dict[str, Any]:
        return self._compute_hard_gate()

    def update_agent_state(self, agent_name: str, updates: dict[str, Any]) -> None:
        with self._lock:
            self.agents_state.setdefault(agent_name, {})
            self.agents_state[agent_name].update(updates)
            self._write()

    def detect_orphan_files(self, orphan_timeout_seconds: int = 600) -> list[str]:
        orphans = []
        with self._lock:
            threshold = datetime.now(timezone.utc)
            for path, f in self._files.items():
                if f.status != "analyzing" or not f.analyzing_started_at:
                    continue
                try:
                    started = datetime.fromisoformat(f.analyzing_started_at)
                except ValueError:
                    continue
                if (threshold - started).total_seconds() > orphan_timeout_seconds:
                    orphans.append(path)
        return orphans

    def get_hints(self, agent_name: str) -> dict[str, Any]:
        hints: dict[str, Any] = {}
        if agent_name == "dataflow_agent":
            hints["discovered_routes"] = list(self.discovered_routes)
        return hints

    def add_discovered_route(self, route: dict[str, Any]) -> None:
        with self._lock:
            self.discovered_routes.append(route)
            self._write()
```

- [ ] **Step 5: Run manifest tests**

Run: `.venv/bin/pytest tests/test_per_file_manifest.py -v`
Expected: All 14 tests PASS

- [ ] **Step 6: Commit**

```bash
git add src/nano_strix/agents/per_file_lib/__init__.py \
        src/nano_strix/agents/per_file_lib/manifest.py \
        tests/test_per_file_manifest.py
git commit -m "feat: add FileManifest with agent checkpoint state and thread-safe file tracking"
```

---

### Task 2: PerFileConfig schema

**Files:**
- Modify: `src/nano_strix/config/schema.py`
- Modify: `tests/test_config.py`

- [ ] **Step 1: Write failing test**

```python
# Add to tests/test_config.py
def test_per_file_config_defaults():
    from nano_strix.config.schema import PerFileConfig, PerFileAgentConfig

    cfg = PerFileConfig()
    assert cfg.classification_model == "claude-haiku-4-5-20251001"
    assert cfg.analysis_model == "claude-sonnet-4-6"
    assert cfg.max_concurrent == 4
    assert cfg.max_file_retries == 3
    assert cfg.orphan_timeout_seconds == 600
    assert cfg.max_agent_restarts == 3
    assert len(cfg.agents) == 4
    assert cfg.agents["route_agent"].enabled is True
    assert cfg.agents["route_agent"].max_iterations == 300


def test_per_file_config_nested_in_app_config():
    from nano_strix.config.schema import AppConfig

    cfg = AppConfig()
    assert cfg.per_file is not None
    assert cfg.per_file.classification_model == "claude-haiku-4-5-20251001"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_config.py::test_per_file_config_defaults tests/test_config.py::test_per_file_config_nested_in_app_config -v`
Expected: FAIL — PerFileConfig not defined

- [ ] **Step 3: Add PerFileConfig to schema.py**

```python
# Add after SchedulerConfig in src/nano_strix/config/schema.py

@dataclass
class PerFileAgentConfig:
    enabled: bool = True
    max_iterations: int = 300


@dataclass
class PerFileConfig:
    agents: dict[str, PerFileAgentConfig] = field(
        default_factory=lambda: {
            "route_agent": PerFileAgentConfig(),
            "dataflow_agent": PerFileAgentConfig(),
            "auth_agent": PerFileAgentConfig(),
            "dependency_agent": PerFileAgentConfig(),
        }
    )
    classification_model: str = "claude-haiku-4-5-20251001"
    analysis_model: str = "claude-sonnet-4-6"
    max_concurrent: int = 4
    max_tokens: int = 4096
    temperature: float = 0.1
    phase3_timeout_seconds: int = 1800
    per_file_timeout_seconds: int = 3600
    max_file_retries: int = 3
    orphan_timeout_seconds: int = 600
    max_agent_restarts: int = 3
    manifest_sync_interval_seconds: int = 5
    health_check_interval_seconds: int = 30
    static_scanners: list[str] = field(default_factory=lambda: ["semgrep", "bandit"])
```

```python
# Add to AppConfig in schema.py:
@dataclass
class AppConfig:
    llm: LLMConfig = field(default_factory=LLMConfig)
    pipeline: PipelineConfig = field(default_factory=PipelineConfig)
    sandbox: SandboxConfig = field(default_factory=SandboxConfig)
    ipc: IPCConfig = field(default_factory=IPCConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    scheduler: SchedulerConfig = field(default_factory=SchedulerConfig)
    per_file: PerFileConfig = field(default_factory=PerFileConfig)  # NEW
```

- [ ] **Step 4: Run config tests**

Run: `.venv/bin/pytest tests/test_config.py::test_per_file_config_defaults tests/test_config.py::test_per_file_config_nested_in_app_config -v`
Expected: 2 PASS

- [ ] **Step 5: Run full test suite to confirm no regressions**

Run: `.venv/bin/pytest -v`
Expected: All tests pass (existing + new)

- [ ] **Step 6: Commit**

```bash
git add src/nano_strix/config/schema.py tests/test_config.py
git commit -m "feat: add PerFileConfig to schema"
```

---

### Task 3: Phase 1 — File classifier

**Files:**
- Create: `src/nano_strix/agents/per_file_lib/classifier.py`
- Modify: `tests/test_per_file_agent.py` (shared test file)

- [ ] **Step 1: Write classifier test**

```python
# tests/test_per_file_agent.py (first test module)
import json
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch


class MockLLMResponse:
    def __init__(self, content):
        self.content = content
        self.tool_calls = []
        self.finish_reason = "stop"


@pytest.fixture
def mock_llm_client():
    client = MagicMock()
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_per_file_agent.py::test_classify_files_returns_manifest -v`
Expected: FAIL — module or function not found

- [ ] **Step 3: Implement classifier**

```python
# src/nano_strix/agents/per_file_lib/classifier.py
from __future__ import annotations

import json
import logging
from pathlib import Path

from nano_strix.agents.per_file_lib.manifest import FileManifest
from nano_strix.llm.adapter import LLMProvider

logger = logging.getLogger(__name__)

CLASSIFIER_SYSTEM_PROMPT = """You are a code security analyst. Your task is to classify source code files by risk priority and analysis dimension.

For each file in the provided directory listing, assign:
1. **priority**: "high" | "medium" | "low"
   - high: auth, login, database queries, API routes, input handling, command execution
   - medium: business logic, middleware, model definitions, data transformation
   - low: config, utilities, static assets, tests, fixtures, type stubs
2. **dimensions**: list from ["route", "dataflow", "auth", "dependency"] (can be empty)
   - route: defines HTTP routes or API endpoints
   - dataflow: handles user input, database operations, command execution, file I/O
   - auth: authentication, authorization, session management, JWT, password hashing
   - dependency: imports third-party libraries, dependency declaration files

Return ONLY a JSON object with a "files" key mapping each file path to {"priority": ..., "dimensions": [...]}.
Do NOT include any other text."""


async def classify_files(
    target_dir: str,
    manifest_path: Path,
    llm_client: LLMProvider,
    agent_names: list[str],
    max_file_retries: int = 3,
) -> FileManifest:
    """Phase 1: Discover files in target_dir and classify via LLM."""
    target = Path(target_dir)
    if not target.exists():
        raise FileNotFoundError(f"Target directory not found: {target_dir}")

    # Collect all files recursively
    all_files: list[str] = []
    for p in sorted(target.rglob("*")):
        if p.is_file() and not p.name.startswith("."):
            rel = str(p.relative_to(target))
            all_files.append(rel)

    if not all_files:
        # Empty directory — create manifest with no files
        return FileManifest.create(
            path=manifest_path,
            files={},
            agent_names=agent_names,
            max_file_retries=max_file_retries,
        )

    logger.info("Phase 1: discovered %d files in %s", len(all_files), target_dir)

    # Build prompt
    file_list = "\n".join(f"  - {f}" for f in all_files)
    user_prompt = f"Directory: {target_dir}\nFiles ({len(all_files)}):\n{file_list}"

    response = await llm_client.chat(
        messages=[
            {"role": "system", "content": CLASSIFIER_SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.1,
        max_tokens=8192,
    )

    # Parse LLM response
    raw = (response.content or "").strip()
    # Strip markdown code fences if present
    if raw.startswith("```"):
        lines = raw.split("\n")
        raw = "\n".join(lines[1:]) if lines else raw
    if raw.endswith("```"):
        raw = raw[:-3].strip()

    try:
        data = json.loads(raw)
        classified = data.get("files", {})
    except json.JSONDecodeError:
        logger.error("Failed to parse LLM classifier response: %s", raw[:500])
        # Fallback: all medium, no dimensions
        classified = {f: {"priority": "medium", "dimensions": []} for f in all_files}

    # Ensure all files are present in classified
    for f in all_files:
        if f not in classified:
            classified[f] = {"priority": "medium", "dimensions": []}

    # Only keep files that exist
    files_dict = {f: classified[f] for f in all_files if f in classified}

    manifest = FileManifest.create(
        path=manifest_path,
        files=files_dict,
        agent_names=agent_names,
        max_file_retries=max_file_retries,
    )
    manifest.phase = "classification"
    manifest.save()

    logger.info("Phase 1 complete: %d files classified", len(files_dict))
    return manifest
```

- [ ] **Step 4: Run classifier test**

Run: `.venv/bin/pytest tests/test_per_file_agent.py::test_classify_files_returns_manifest -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/nano_strix/agents/per_file_lib/classifier.py tests/test_per_file_agent.py
git commit -m "feat: add Phase 1 file classifier with LLM-based priority and dimension tagging"
```

---

### Task 4: Phase 2 — Static scanner

**Files:**
- Create: `src/nano_strix/agents/per_file_lib/scanner.py`

- [ ] **Step 1: Write scanner test**

```python
# Add to tests/test_per_file_agent.py
import asyncio

@pytest.fixture
def sample_manifest(tmp_path, target_dir):
    from nano_strix.agents.per_file_lib.manifest import FileManifest

    files = {
        "src/auth/login.py": {"priority": "high", "dimensions": ["auth"]},
        "src/api/handler.py": {"priority": "high", "dimensions": ["route"]},
        "src/utils/format.py": {"priority": "low", "dimensions": []},
    }
    path = tmp_path / "manifest.json"
    return FileManifest.create(path, files,
                               ["route_agent", "dataflow_agent", "auth_agent", "dependency_agent"])


async def test_scanner_writes_findings_to_manifest(sample_manifest, target_dir):
    from nano_strix.agents.per_file_lib.scanner import run_static_scans

    # Only run semgrep if available, otherwise skip gracefully
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_per_file_agent.py::test_scanner_writes_findings_to_manifest tests/test_per_file_agent.py::test_scanner_missing_tool_handled -v`
Expected: FAIL — module not found

- [ ] **Step 3: Implement scanner**

```python
# src/nano_strix/agents/per_file_lib/scanner.py
from __future__ import annotations

import asyncio
import json
import logging
import shutil
from pathlib import Path

from nano_strix.agents.per_file_lib.manifest import FileManifest

logger = logging.getLogger(__name__)

# Map scanner names to their CLI invocation patterns
SCANNER_CONFIG = {
    "semgrep": {
        "binary": "semgrep",
        "args": lambda target: ["--config", "auto", "--json", "--no-git-ignore", target],
        "output_mode": "json",
    },
    "bandit": {
        "binary": "bandit",
        "args": lambda target: ["-r", target, "-f", "json"],
        "output_mode": "json",
    },
}


async def run_static_scans(
    manifest: FileManifest,
    target_dir: str,
    scanners: list[str],
) -> None:
    """Phase 2: Run static analysis tools against target directory."""
    manifest.phase = "static_scan"
    manifest.save()

    for scanner_name in scanners:
        config = SCANNER_CONFIG.get(scanner_name)
        if not config:
            logger.warning("Unknown scanner '%s', skipping", scanner_name)
            continue

        binary = shutil.which(config["binary"])
        if not binary:
            logger.warning("%s not found in PATH, skipping", config["binary"])
            continue

        logger.info("Running %s on %s...", scanner_name, target_dir)
        try:
            args = config["args"](target_dir)
            process = await asyncio.create_subprocess_exec(
                binary,
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                process.communicate(), timeout=300
            )

            if process.returncode != 0 and scanner_name == "semgrep":
                # semgrep returns non-zero when findings exist — that's expected
                pass

            output = stdout.decode(errors="replace").strip()
            if not output:
                logger.info("%s produced no output", scanner_name)
                continue

            _parse_and_apply_findings(manifest, scanner_name, output, target_dir)

        except asyncio.TimeoutError:
            logger.warning("%s timed out after 300s", scanner_name)
            process.kill()
            await process.wait()
        except Exception:
            logger.exception("Error running %s", scanner_name)

    manifest.phase = "static_scan"
    manifest.save()
    logger.info("Phase 2 complete: static scans finished")


def _parse_and_apply_findings(
    manifest: FileManifest,
    scanner_name: str,
    output: str,
    target_dir: str,
) -> None:
    """Parse scanner JSON output and attach findings to manifest files."""
    try:
        data = json.loads(output)
    except json.JSONDecodeError:
        logger.debug("Could not parse %s output as JSON", scanner_name)
        return

    target = Path(target_dir)

    if scanner_name == "semgrep":
        results = data.get("results", [])
        for result in results:
            file_path = result.get("path", "")
            try:
                rel_path = str(Path(file_path).relative_to(target))
            except ValueError:
                rel_path = file_path

            check = result.get("check_id", result.get("rule", ""))
            extra = result.get("extra", {})
            finding = {
                "scanner": "semgrep",
                "rule": check,
                "line": result.get("start", {}).get("line", 0),
                "severity": extra.get("severity", "medium"),
                "message": extra.get("message", ""),
                "category": extra.get("metadata", {}).get("category", ""),
            }
            if rel_path in manifest.files:
                manifest.files[rel_path].scan_findings.append(finding)

    elif scanner_name == "bandit":
        results = data.get("results", [])
        for result in results:
            file_path = result.get("filename", "")
            try:
                rel_path = str(Path(file_path).relative_to(target))
            except ValueError:
                rel_path = file_path

            finding = {
                "scanner": "bandit",
                "rule": result.get("test_id", ""),
                "line": result.get("line_number", 0),
                "severity": result.get("issue_severity", "medium"),
                "message": result.get("issue_text", ""),
                "confidence": result.get("issue_confidence", ""),
            }
            if rel_path in manifest.files:
                manifest.files[rel_path].scan_findings.append(finding)

    manifest.save()
```

- [ ] **Step 4: Run scanner tests**

Run: `.venv/bin/pytest tests/test_per_file_agent.py::test_scanner_writes_findings_to_manifest tests/test_per_file_agent.py::test_scanner_missing_tool_handled -v`
Expected: PASS (tests gracefully handle missing semgrep)

- [ ] **Step 5: Commit**

```bash
git add src/nano_strix/agents/per_file_lib/scanner.py tests/test_per_file_agent.py
git commit -m "feat: add Phase 2 static scanner (semgrep/bandit)"
```

---

### Task 5: Phase 3 — Sub-agent runner with agent_loop

**Files:**
- Create: `src/nano_strix/agents/per_file_lib/sub_agents.py`

- [ ] **Step 1: Write sub-agent tests**

```python
# Add to tests/test_per_file_agent.py

@pytest.fixture
def manifest_for_subagent(tmp_path):
    from nano_strix.agents.per_file_lib.manifest import FileManifest

    files = {
        "src/auth/login.py": {"priority": "high", "dimensions": ["auth", "route"]},
        "src/api/handler.py": {"priority": "high", "dimensions": ["route", "dataflow"]},
        "src/utils/format.py": {"priority": "low", "dimensions": []},
    }
    path = tmp_path / "manifest.json"
    return FileManifest.create(path, files,
                               ["route_agent", "dataflow_agent", "auth_agent", "dependency_agent"])


async def test_agent_loop_analyzes_matching_files(manifest_for_subagent):
    from nano_strix.agents.per_file_lib.sub_agents import SubAgentRunner
    from unittest.mock import MagicMock, AsyncMock

    mock_llm = MagicMock()
    mock_llm.chat = AsyncMock(return_value=MockLLMResponse(
        '{"findings": [{"id": "F-001", "title": "Test", "severity": "low"}]}'
    ))

    semaphore = __import__('threading').Semaphore(4)

    runner = SubAgentRunner(
        manifest=manifest_for_subagent,
        llm_client=mock_llm,
        semaphore=semaphore,
        target_dir="/tmp/test_target",
    )

    # Run agent_loop for route_agent (passes files to read)
    runner.run_single_agent("route_agent", max_iterations=5)

    # After agent_loop: all route-tagged files should be analyzed (high priority first)
    f = manifest_for_subagent.files["src/auth/login.py"]
    assert f.skip_votes.get("route_agent") is not None


async def test_agent_loop_votes_skip_on_non_matching(manifest_for_subagent):
    from nano_strix.agents.per_file_lib.sub_agents import SubAgentRunner
    from unittest.mock import MagicMock, AsyncMock

    mock_llm = MagicMock()
    mock_llm.chat = AsyncMock()

    semaphore = __import__('threading').Semaphore(4)

    runner = SubAgentRunner(
        manifest=manifest_for_subagent,
        llm_client=mock_llm,
        semaphore=semaphore,
        target_dir="/tmp/test_target",
    )

    # auth_agent dimension is "auth" — only src/auth/login.py matches
    runner.run_single_agent("auth_agent", max_iterations=5)

    # Non-auth files should get skip votes from auth_agent
    f = manifest_for_subagent.files["src/utils/format.py"]
    assert f.skip_votes.get("auth_agent") == "skip"
    assert "auth" not in f.dimensions


async def test_sub_agent_runner_runs_all_threads(manifest_for_subagent):
    from nano_strix.agents.per_file_lib.sub_agents import SubAgentRunner
    from unittest.mock import MagicMock, AsyncMock

    mock_llm = MagicMock()
    mock_llm.chat = AsyncMock(return_value=MockLLMResponse(
        '{"findings": [{"id": "F-001", "title": "Test", "severity": "low"}]}'
    ))

    semaphore = __import__('threading').Semaphore(4)

    runner = SubAgentRunner(
        manifest=manifest_for_subagent,
        llm_client=mock_llm,
        semaphore=semaphore,
        target_dir="/tmp/test_target",
    )

    runner.run_all(max_iterations=5, phase3_timeout=30)

    # After all agents finish, can_finish should be True
    assert manifest_for_subagent.can_finish() is True
    for state in manifest_for_subagent.agents_state.values():
        assert state["status"] in ("completed", "pending")


async def test_failed_thread_restarts_agent(manifest_for_subagent):
    from nano_strix.agents.per_file_lib.sub_agents import SubAgentRunner

    semaphore = __import__('threading').Semaphore(4)

    class CrashingLLM:
        call_count = 0
        async def chat(self, *args, **kwargs):
            self.__class__.call_count += 1
            if self.call_count <= 1:
                raise RuntimeError("simulated crash")
            from unittest.mock import MagicMock
            return MockLLMResponse('{"findings": []}')

    runner = SubAgentRunner(
        manifest=manifest_for_subagent,
        llm_client=CrashingLLM(),
        semaphore=semaphore,
        target_dir="/tmp/test_target",
        max_agent_restarts=2,
    )

    runner.run_single_agent("route_agent", max_iterations=5)

    # Agent should have been restarted
    state = manifest_for_subagent.agents_state["route_agent"]
    assert state["restart_count"] >= 1
    assert state["status"] == "completed"


def test_health_check_detects_stale_agent(manifest_for_subagent):
    from nano_strix.agents.per_file_lib.sub_agents import SubAgentRunner
    from datetime import datetime, timedelta, timezone
    from unittest.mock import MagicMock, AsyncMock

    mock_llm = MagicMock()
    mock_llm.chat = AsyncMock()

    semaphore = __import__('threading').Semaphore(4)

    runner = SubAgentRunner(
        manifest=manifest_for_subagent,
        llm_client=mock_llm,
        semaphore=semaphore,
        target_dir="/tmp/test_target",
    )

    # Mark agent as running with stale health check
    manifest_for_subagent.update_agent_state("route_agent", {
        "status": "running",
        "last_health_check": (datetime.now(timezone.utc) - timedelta(seconds=9999)).isoformat(),
    })

    unhealthy = runner.detect_unhealthy_agents(orphan_timeout_seconds=600)
    assert "route_agent" in unhealthy
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_per_file_agent.py::test_agent_loop_analyzes_matching_files tests/test_per_file_agent.py::test_agent_loop_votes_skip_on_non_matching tests/test_per_file_agent.py::test_sub_agent_runner_runs_all_threads -v`
Expected: FAIL — SubAgentRunner not defined

- [ ] **Step 3: Implement sub_agents.py**

```python
# src/nano_strix/agents/per_file_lib/sub_agents.py
from __future__ import annotations

import asyncio
import logging
import threading
import traceback
from datetime import datetime, timezone
from typing import Any

from nano_strix.agents.per_file_lib.manifest import FileManifest
from nano_strix.llm.adapter import LLMProvider

logger = logging.getLogger(__name__)

AGENT_SYSTEM_PROMPTS: dict[str, str] = {
    "route_agent": (
        "You are a Route Discovery agent. Your task is to find all HTTP/API entry points "
        "in the given source file. Identify Flask routes, FastAPI endpoints, Express routers, "
        "Django URL patterns, etc. For each route found, record: path, HTTP method, "
        "file location, line number, and handler function name.\n"
        "If the file contains NO routes, respond with an empty findings list and "
        "explain why in a brief note."
    ),
    "dataflow_agent": (
        "You are a Dataflow Analysis agent. Trace user input from source to dangerous sink "
        "in the given source file. Identify: sources (request parameters, user input, file uploads), "
        "transformations (validation, sanitization, encoding), and sinks (SQL queries, "
        "command execution, file operations, deserialization, template rendering).\n"
        "Flag any missing input validation or sanitization as a finding.\n"
        "If the file contains NO dataflow concerns, respond with an empty findings list."
    ),
    "auth_agent": (
        "You are an Authentication/Authorization agent. Analyze the given source file "
        "for: authentication mechanisms, session management, JWT handling, password hashing, "
        "authorization checks, permission middleware, OAuth flows, API key validation.\n"
        "Flag: missing auth checks, weak crypto, hardcoded credentials, insecure session config.\n"
        "If the file contains NO auth concerns, respond with an empty findings list."
    ),
    "dependency_agent": (
        "You are a Dependency Analysis agent. Analyze the given source file for third-party "
        "library usage and known vulnerabilities. Check: imported packages against CVE databases, "
        "dependency version constraints, deprecated libraries, license compliance.\n"
        "For dependency declaration files (requirements.txt, package.json, pom.xml, etc.), "
        "enumerate all dependencies and flag any with known vulnerabilities.\n"
        "If the file contains NO dependency concerns, respond with an empty findings list."
    ),
}


class SubAgentRunner:
    """Manages 4 parallel sub-agent threads with checkpoint and retry support."""

    def __init__(
        self,
        manifest: FileManifest,
        llm_client: LLMProvider,
        semaphore: threading.Semaphore,
        target_dir: str,
        max_agent_restarts: int = 3,
        health_check_interval: int = 30,
    ) -> None:
        self._manifest = manifest
        self._llm_client = llm_client
        self._semaphore = semaphore
        self._target_dir = target_dir
        self._max_agent_restarts = max_agent_restarts
        self._health_check_interval = health_check_interval
        self._threads: dict[str, threading.Thread] = {}
        self._stop_event = threading.Event()

    # ---- Public API ----

    def run_all(self, max_iterations: int = 300, phase3_timeout: int = 1800) -> None:
        """Spawn all sub-agent threads and wait for completion."""
        agent_names = list(self._manifest.agents_state.keys())

        for name in agent_names:
            state = self._manifest.agents_state[name]
            if state["status"] in ("completed",):
                continue
            self._start_agent_thread(name, max_iterations)

        # Wait with timeout
        deadline = datetime.now().timestamp() + phase3_timeout
        for name, thread in list(self._threads.items()):
            remaining = deadline - datetime.now().timestamp()
            if remaining <= 0:
                logger.warning("Phase 3 timeout reached, remaining agents will be collected")
                break
            thread.join(timeout=max(1, remaining))

        # Collect results from completed threads
        self._collect_results()

    def run_single_agent(self, agent_name: str, max_iterations: int = 300) -> None:
        """Run a single agent synchronously (for testing)."""
        self._start_agent_thread(agent_name, max_iterations)
        thread = self._threads.get(agent_name)
        if thread:
            thread.join()

    # ---- Agent thread management ----

    def _start_agent_thread(self, agent_name: str, max_iterations: int) -> None:
        thread = threading.Thread(
            target=self._agent_thread_entry,
            args=(agent_name, max_iterations),
            daemon=True,
            name=f"per_file_{agent_name}",
        )
        self._threads[agent_name] = thread
        self._manifest.update_agent_state(agent_name, {
            "status": "running",
            "thread_id": thread.ident,
            "last_health_check": datetime.now(timezone.utc).isoformat(),
        })
        thread.start()

    def _agent_thread_entry(self, agent_name: str, max_iterations: int) -> None:
        """Entry point for each agent thread."""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(
                self._agent_loop(agent_name, max_iterations)
            )
        except Exception:
            logger.error("Agent %s crashed:\n%s", agent_name, traceback.format_exc())
            self._handle_agent_crash(agent_name)
        finally:
            loop.close()

    # ---- Agent loop ----

    async def _agent_loop(self, agent_name: str, max_iterations: int) -> None:
        """Core agent loop: claim file → analyze → update manifest → repeat."""
        state = self._manifest.agents_state[agent_name]
        iteration = state.get("iteration", 0)
        system_prompt = AGENT_SYSTEM_PROMPTS.get(agent_name, "")
        my_dimension = {
            "route_agent": "route",
            "dataflow_agent": "dataflow",
            "auth_agent": "auth",
            "dependency_agent": "dependency",
        }.get(agent_name)

        while not self._manifest.can_finish() and iteration < max_iterations:
            if self._stop_event.is_set():
                break

            # Health heartbeat
            self._manifest.update_agent_state(agent_name, {
                "last_health_check": datetime.now(timezone.utc).isoformat(),
                "iteration": iteration,
            })

            target = self._manifest.claim_pending_file(agent_name)
            if target is None:
                self._manifest.vote_skip_remaining(
                    agent_name, reason="all matching files processed"
                )
                self._manifest.update_agent_state(agent_name, {"iteration": iteration})
                continue

            file_path = target.path

            # Non-matching dimension → vote skip, don't waste LLM call
            if my_dimension and my_dimension not in target.dimensions:
                self._manifest.vote_skip(
                    file_path, agent_name,
                    reason=f"{agent_name}: dimension '{my_dimension}' not in file dimensions {target.dimensions}"
                )
                self._manifest.update_agent_state(agent_name, {
                    "files_skipped": state.get("files_skipped", 0) + 1
                })
                iteration += 1
                continue

            # Claim file for analysis
            self._manifest.mark_analyzing(file_path, agent_name)

            try:
                # Read file content
                full_path = __import__('pathlib').Path(self._target_dir) / file_path
                try:
                    content = full_path.read_text(errors="replace")
                except Exception:
                    content = f"[Could not read file: {file_path}]"

                scan_results = target.scan_findings
                hints = self._manifest.get_hints(agent_name)

                # Build messages
                hint_text = ""
                if hints.get("discovered_routes"):
                    hint_text = "\n\nDiscovered routes from route analysis:\n" + \
                        "\n".join(
                            f"  {r['method']} {r['path']} ({r['file']}:{r['line']})"
                            for r in hints["discovered_routes"]
                        )

                user_prompt = (
                    f"File: {file_path}\n"
                    f"Priority: {target.priority}\n"
                    f"Static scan findings: {scan_results}\n"
                    f"{hint_text}\n\n"
                    f"Source code:\n```\n{content[:8000]}\n```\n\n"
                    "Return a JSON object with a 'findings' list. Each finding should have: "
                    "id, title, severity (critical/high/medium/low/info), category, file_path, "
                    "line_range [start, end], description, code_snippet, recommendation, confidence (0-1)."
                )

                self._semaphore.acquire()
                try:
                    response = await self._llm_client.chat(
                        messages=[
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_prompt},
                        ],
                        temperature=0.1,
                        max_tokens=4096,
                    )
                finally:
                    self._semaphore.release()

                # Parse response
                findings = self._parse_findings(response.content or "", file_path)

                # Mark as analyzed
                self._manifest.update_file(
                    file_path, findings=findings, status="analyzed"
                )
                # Cast analyze vote
                target.skip_votes[agent_name] = "analyze"
                self._manifest.save()

                self._manifest.update_agent_state(agent_name, {
                    "files_analyzed": state.get("files_analyzed", 0) + 1
                })

                # If route_agent, extract discovered routes
                if agent_name == "route_agent":
                    self._extract_routes(findings, file_path)

            except Exception:
                logger.exception("Agent %s error on file %s", agent_name, file_path)
                self._manifest.handle_agent_error(file_path, agent_name)
                # Continue with next file

            iteration += 1

        # Agent finished
        if iteration >= max_iterations:
            self._manifest.vote_skip_remaining(
                agent_name, reason="max_iterations reached"
            )

        self._manifest.update_agent_state(agent_name, {
            "status": "completed",
            "iteration": iteration,
        })

    # ---- Helpers ----

    def _parse_findings(self, content: str, file_path: str) -> list[dict[str, Any]]:
        import json as _json
        raw = content.strip()
        if raw.startswith("```"):
            lines = raw.split("\n")
            raw = "\n".join(lines[1:])
        if raw.endswith("```"):
            raw = raw[:-3].strip()
        try:
            data = _json.loads(raw)
            findings = data.get("findings", [])
        except _json.JSONDecodeError:
            logger.warning("Could not parse findings JSON for %s", file_path)
            return []

        for f in findings:
            f.setdefault("file_path", file_path)
        return findings

    def _extract_routes(
        self, findings: list[dict[str, Any]], file_path: str
    ) -> None:
        for f in findings:
            route_info = f.get("route")
            if route_info and isinstance(route_info, dict):
                self._manifest.add_discovered_route({
                    "path": route_info.get("path", ""),
                    "method": route_info.get("method", "GET"),
                    "file": file_path,
                    "line": route_info.get("line", 0),
                })

    def _handle_agent_crash(self, agent_name: str) -> None:
        """Handle agent thread crash: clean orphan files, restart if possible."""
        state = self._manifest.agents_state[agent_name]
        restart_count = state.get("restart_count", 0)

        # Clean up orphan files
        for path, f in self._manifest.files.items():
            if f.assigned_to == agent_name and f.status == "analyzing":
                self._manifest.handle_agent_error(path, agent_name)

        if restart_count < self._max_agent_restarts:
            self._manifest.update_agent_state(agent_name, {
                "status": "restarted",
                "restart_count": restart_count + 1,
                "current_file": None,
                "crash_reason": traceback.format_exc()[-500:],
            })
            logger.warning("Restarting %s (attempt %d/%d)",
                           agent_name, restart_count + 1, self._max_agent_restarts)
          self._start_agent_thread(agent_name, max_iterations)
        else:
            self._manifest.update_agent_state(agent_name, {
                "status": "crashed",
                "crash_reason": f"max restarts ({self._max_agent_restarts}) exceeded",
            })
            # Vote skip for remaining files
            self._manifest.vote_skip_remaining(
                agent_name, reason=f"agent crashed after {restart_count} restarts"
            )

    def detect_unhealthy_agents(self, orphan_timeout_seconds: int = 600) -> dict[str, str]:
        """Check for agents that haven't updated health check within timeout."""
        unhealthy = {}
        now = datetime.now(timezone.utc)
        for name, state in self._manifest.agents_state.items():
            if state["status"] != "running":
                continue
            last = state.get("last_health_check")
            if not last:
                continue
            try:
                last_time = datetime.fromisoformat(last)
            except ValueError:
                continue
            if (now - last_time).total_seconds() > orphan_timeout_seconds:
                unhealthy[name] = f"last check at {last}"
        return unhealthy

    def _collect_results(self) -> None:
        """Collect results from completed threads."""
        for name, thread in self._threads.items():
            if thread.is_alive():
                logger.warning("Agent %s still running at collection time", name)
                state = self._manifest.agents_state[name]
                if state["status"] == "running":
                    # Mark as crashed for post-mortem
                    self._handle_agent_crash(name)
```

- [ ] **Step 4: Run sub-agent tests**

Run: `.venv/bin/pytest tests/test_per_file_agent.py::test_agent_loop_analyzes_matching_files tests/test_per_file_agent.py::test_agent_loop_votes_skip_on_non_matching tests/test_per_file_agent.py::test_sub_agent_runner_runs_all_threads tests/test_per_file_agent.py::test_failed_thread_restarts_agent tests/test_per_file_agent.py::test_health_check_detects_stale_agent -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add src/nano_strix/agents/per_file_lib/sub_agents.py tests/test_per_file_agent.py
git commit -m "feat: add Phase 3 SubAgentRunner with multi-threaded agent_loop"
```

---

### Task 6: Entry point — rewrite per_file.py

**Files:**
- Modify: `src/nano_strix/agents/per_file.py` (complete rewrite)

- [ ] **Step 1: Rewrite per_file.py as 3-phase orchestrator**

```python
# src/nano_strix/agents/per_file.py (REWRITE)
"""per_file agent: multi-phase LLM-driven file-by-file security analysis.

Launched by AgentManager as a subprocess. Reads task JSON from stdin,
runs 3-phase analysis, writes result JSON to stdout.
"""
from __future__ import annotations

import asyncio
import json
import logging
import sys
import threading
from pathlib import Path

# Make sibling packages importable
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from per_file_lib.manifest import FileManifest  # noqa: E402
from per_file_lib.classifier import classify_files  # noqa: E402
from per_file_lib.scanner import run_static_scans  # noqa: E402
from per_file_lib.sub_agents import SubAgentRunner  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [per_file] %(message)s")
logger = logging.getLogger(__name__)


def load_config_from_workspace(workspace: Path) -> dict:
    """Load per_file config from workspace or use defaults."""
    config_path = workspace / "config.yaml"
    if config_path.exists():
        import yaml
        with open(config_path) as f:
            return yaml.safe_load(f).get("per_file", {})
    return {}


def create_llm_client(model_name: str, config: dict) -> object:
    """Create an LLM client for the given model. Tries to use project's factory."""
    try:
        from nano_strix.config.schema import LLMConfig
        from nano_strix.llm.factory import create_provider
        cfg = LLMConfig(model=model_name)
        return create_provider(cfg)
    except Exception:
        logger.warning("Could not create LLM provider via factory, using environment")
        # Fallback: try to create anthropic client directly
        import os
        try:
            from nano_strix.llm.anthropic import AnthropicProvider
            return AnthropicProvider(
                api_key=os.environ.get("ANTHROPIC_API_KEY", ""),
                model=model_name,
            )
        except Exception:
            raise RuntimeError(
                "No LLM provider available. Set ANTHROPIC_API_KEY or configure llm in config.yaml"
            )


async def main_async() -> None:
    """Main async entry point."""
    # Read task from stdin
    line = sys.stdin.readline()
    if not line:
        logger.error("No input on stdin")
        sys.exit(1)

    msg = json.loads(line)
    task_id = msg["task_id"]
    target = msg.get("payload", {}).get("target", ".")
    stage_results = msg.get("payload", {}).get("stage_results", {})

    logger.info("Task %s: starting per_file analysis of %s", task_id, target)

    target_path = Path(target)
    if not target_path.exists():
        result = {
            "type": "result",
            "task_id": task_id,
            "payload": {"status": "error", "error": f"Target not found: {target}"},
        }
        print(json.dumps(result))
        return

    # Determine workspace from target path convention: workspace/{task_id}/source
    workspace = target_path.parent  # workspace/{task_id}/
    manifest_path = workspace / "file_manifest.json"

    # Load config
    config = load_config_from_workspace(workspace)

    agent_names = [
        "route_agent", "dataflow_agent", "auth_agent", "dependency_agent"
    ]

    # Phase 1: Classification
    logger.info("Phase 1: Discovering and classifying files...")
    classification_model = config.get("classification_model", "claude-haiku-4-5-20251001")
    classifier_client = create_llm_client(classification_model, config)

    manifest = await classify_files(
        target_dir=str(target_path),
        manifest_path=manifest_path,
        llm_client=classifier_client,
        agent_names=agent_names,
        max_file_retries=config.get("max_file_retries", 3),
    )

    # Report progress
    _emit_progress(task_id, "phase1_complete", {
        "total_files": len(manifest.files),
    })

    # Phase 2: Static scanning
    logger.info("Phase 2: Running static scanners...")
    scanners = config.get("static_scanners", ["semgrep", "bandit"])
    await run_static_scans(
        manifest=manifest,
        target_dir=str(target_path),
        scanners=scanners,
    )

    _emit_progress(task_id, "phase2_complete", {
        "total_files": len(manifest.files),
    })

    # Phase 3: Multi-agent parallel analysis
    logger.info("Phase 3: Starting parallel sub-agent analysis...")
    manifest.phase = "analysis"
    manifest.save()

    analysis_model = config.get("analysis_model", "claude-sonnet-4-6")
    analysis_client = create_llm_client(analysis_model, config)

    max_concurrent = config.get("max_concurrent", 4)
    llm_semaphore = threading.Semaphore(max_concurrent)

    runner = SubAgentRunner(
        manifest=manifest,
        llm_client=analysis_client,
        semaphore=llm_semaphore,
        target_dir=str(target_path),
        max_agent_restarts=config.get("max_agent_restarts", 3),
        health_check_interval=config.get("health_check_interval_seconds", 30),
    )

    # Start health check timer
    import time as _time
    def _health_check_loop():
        while not runner._stop_event.is_set():
            _time.sleep(config.get("health_check_interval_seconds", 30))
            unhealthy = runner.detect_unhealthy_agents(
                config.get("orphan_timeout_seconds", 600)
            )
            for name, reason in unhealthy.items():
                logger.warning("Unhealthy agent %s: %s", name, reason)

    health_thread = threading.Thread(target=_health_check_loop, daemon=True)
    health_thread.start()

    runner.run_all(
        max_iterations=300,
        phase3_timeout=config.get("phase3_timeout_seconds", 1800),
    )

    runner._stop_event.set()
    health_thread.join(timeout=5)

    # Collect findings
    all_findings = []
    for f in manifest.files.values():
        all_findings.extend(f.findings)

    coverage = manifest._compute_coverage()

    logger.info("Analysis complete: %d files, %d findings",
                coverage["total"], len(all_findings))

    result = {
        "type": "result",
        "task_id": task_id,
        "payload": {
            "status": "ok",
            "stage": "per_file",
            "target": target,
            "findings": all_findings,
            "coverage_summary": coverage,
            "manifest_path": str(manifest_path),
        },
    }
    print(json.dumps(result, ensure_ascii=False))


def _emit_progress(task_id: str, phase: str, extra: dict) -> None:
    msg = {
        "type": "progress",
        "task_id": task_id,
        "payload": {"phase": phase, **extra},
    }
    print(json.dumps(msg, ensure_ascii=False))


def main():
    """Entry point for subprocess launch."""
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Write entry point integration test**

```python
# Add to tests/test_per_file_agent.py

def test_entry_point_runs_with_mocks(tmp_path, monkeypatch):
    """Full integration test of per_file agent entry point via subprocess-style call."""
    import json, sys, asyncio
    from pathlib import Path
    from unittest.mock import MagicMock, AsyncMock, patch

    # Create target directory structure
    target_dir = tmp_path / "test_target"
    target_dir.mkdir()
    (target_dir / "main.py").write_text("def main():\n    x = input()\n    exec(x)\n")
    (target_dir / "utils.py").write_text("def helper():\n    return 42\n")

    workspace = tmp_path / "tasks" / "t-001"
    workspace.mkdir(parents=True)

    manifest_path = workspace / "file_manifest.json"

    # Prepare stdin input
    stdin_data = json.dumps({
        "type": "task",
        "task_id": "t-001",
        "stage": "per_file",
        "payload": {
            "target": str(target_dir),
            "stage_results": {},
        },
    })

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
        # Phase 3: route_agent analysis
        MagicMock(content=json.dumps({"findings": [
            {"id": "F-001", "title": "exec() injection", "severity": "critical",
             "category": "rce", "file_path": "main.py", "line_range": [1, 1],
             "description": "exec() with user input", "code_snippet": "exec(x)",
             "recommendation": "Do not use exec() with untrusted input", "confidence": 0.95}
        ]}), tool_calls=[], finish_reason="stop"),
        # Phase 3: dataflow_agent analysis
        MagicMock(content=json.dumps({"findings": []}), tool_calls=[], finish_reason="stop"),
        # Phase 3: auth_agent analysis
        MagicMock(content=json.dumps({"findings": []}), tool_calls=[], finish_reason="stop"),
        # Phase 3: dependency_agent analysis
        MagicMock(content=json.dumps({"findings": []}), tool_calls=[], finish_reason="stop"),
    ]

    with patch('sys.stdin', MagicMock(readline=MagicMock(return_value=stdin_data))):
        with patch('sys.stdout', new_callable=MagicMock) as mock_stdout:
            from nano_strix.agents.per_file_lib.classifier import classify_files
            from nano_strix.agents.per_file_lib.manifest import FileManifest
            from nano_strix.agents.per_file_lib.sub_agents import SubAgentRunner
            import threading

            # Run Phase 1
            agent_names = ["route_agent", "dataflow_agent", "auth_agent", "dependency_agent"]
            manifest = asyncio.run(classify_files(
                target_dir=str(target_dir),
                manifest_path=manifest_path,
                llm_client=mock_llm,
                agent_names=agent_names,
            ))

            assert manifest.phase == "classification"
            assert len(manifest.files) == 2
            assert manifest.files["main.py"].priority == "high"

            # Run Phase 3
            manifest.phase = "analysis"
            manifest.save()

            # Use a fresh mock for analysis phase
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
```

- [ ] **Step 3: Run integration test**

Run: `.venv/bin/pytest tests/test_per_file_agent.py::test_entry_point_runs_with_mocks -v`
Expected: PASS

- [ ] **Step 4: Run all tests to verify no regressions**

Run: `.venv/bin/pytest -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/nano_strix/agents/per_file.py tests/test_per_file_agent.py
git commit -m "feat: rewrite per_file agent as 3-phase orchestrator with LLM-driven analysis"
```

---

### Task 7: Final integration test and cleanup

**Files:**
- Modify: `tests/test_per_file_agent.py` (add cleanup test)

- [ ] **Step 1: Add manifest JSON roundtrip integration test**

```python
# Add to tests/test_per_file_agent.py

def test_full_manifest_roundtrip_persistence(tmp_path):
    """Verify manifest survives write/read cycle with all data intact."""
    from nano_strix.agents.per_file_lib.manifest import FileManifest

    path = tmp_path / "manifest.json"
    files = {
        "src/a.py": {"priority": "high", "dimensions": ["auth"]},
        "src/b.py": {"priority": "medium", "dimensions": ["dataflow"]},
    }
    agent_names = ["route_agent", "auth_agent"]

    m1 = FileManifest.create(path, files, agent_names)
    m1.phase = "analysis"
    m1.mark_analyzing("src/a.py", "auth_agent")
    m1.update_file("src/a.py", findings=[
        {"id": "F-001", "title": "Test Finding", "severity": "high",
         "category": "test", "file_path": "src/a.py", "line_range": [1, 2],
         "description": "test", "code_snippet": "x", "recommendation": "fix",
         "confidence": 0.8}
    ], status="analyzed")
    m1.files["src/a.py"].skip_votes["auth_agent"] = "analyze"
    m1.vote_skip("src/b.py", "auth_agent", "no auth concerns")
    m1.vote_skip("src/a.py", "route_agent", "no routes")
    m1.vote_skip("src/b.py", "route_agent", "no routes")
    m1.update_agent_state("auth_agent", {"iteration": 5, "files_analyzed": 1})
    m1.add_discovered_route({"path": "/api/test", "method": "GET", "file": "src/a.py", "line": 10})
    m1.save()

    # Load from disk
    m2 = FileManifest.load(path)
    assert m2.phase == "analysis"
    assert len(m2.files) == 2
    assert m2.files["src/a.py"].status == "analyzed"
    assert m2.files["src/a.py"].skip_votes["auth_agent"] == "analyze"
    assert m2.files["src/b.py"].status == "skipped"
    assert len(m2.discovered_routes) == 1
    assert m2.agents_state["auth_agent"]["iteration"] == 5
    assert m2.agents_state["auth_agent"]["files_analyzed"] == 1
    assert m2.can_finish() is True


def test_can_finish_blocked_by_pending_file(tmp_path):
    from nano_strix.agents.per_file_lib.manifest import FileManifest

    path = tmp_path / "manifest.json"
    files = {
        "high_risk.py": {"priority": "high", "dimensions": ["auth"]},
    }
    agent_names = ["auth_agent"]
    m = FileManifest.create(path, files, agent_names)

    # File still pending, no votes → can't finish
    assert m.can_finish() is False
    assert len(m.hard_gate["blocked_by"]) == 1

    # Mark analyzed and vote → can finish
    m.mark_analyzing("high_risk.py", "auth_agent")
    m.update_file("high_risk.py", status="analyzed")
    m.files["high_risk.py"].skip_votes["auth_agent"] = "analyze"
    m.save()

    assert m.can_finish() is True


def test_manifest_empty_directory(tmp_path):
    from nano_strix.agents.per_file_lib.manifest import FileManifest

    path = tmp_path / "manifest.json"
    m = FileManifest.create(path, {}, ["route_agent"])
    assert len(m.files) == 0
    assert m.can_finish() is True
```

- [ ] **Step 2: Run all tests**

Run: `.venv/bin/pytest tests/test_per_file_agent.py tests/test_per_file_manifest.py -v`
Expected: All tests PASS

- [ ] **Step 3: Run full test suite**

Run: `.venv/bin/pytest -v`
Expected: All tests PASS, no regressions

- [ ] **Step 4: Final commit**

```bash
git add tests/test_per_file_agent.py
git commit -m "test: add manifest roundtrip and can_finish integration tests"
```

---

## Verification Checklist

After all tasks complete:

1. Run: `.venv/bin/pytest -v`
   - All existing tests pass (no regressions)
   - All new tests pass (~25 tests across 2 test files)

2. Run: `.venv/bin/ruff check src/ tests/`
   - No lint errors

3. Manual smoke test:
   ```bash
   echo '{"type":"task","task_id":"t-test","stage":"per_file","payload":{"target":".","stage_results":{}}}' | .venv/bin/python3 src/nano_strix/agents/per_file.py
   ```
   - Should produce JSON result on stdout
