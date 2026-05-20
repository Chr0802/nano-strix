from __future__ import annotations

import json
import threading
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
    _path: str = field(default="", repr=False)

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
                _path=file_path,
            )
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
        """Atomically select and reserve next file for agent. Returns None when all files voted."""
        my_dimension = AGENT_DIMENSIONS.get(agent_name)
        with self._lock:
            candidates = []
            for path, f in self._files.items():
                vote = f.skip_votes.get(agent_name)
                if vote is not None:
                    continue  # already voted
                if f.status in ("analyzed", "skipped"):
                    continue  # already finalized
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
            best_path, best_file = candidates[0]

            # Atomically reserve the file within the lock
            best_file.status = "analyzing"
            best_file.assigned_to = agent_name
            best_file.analyzing_started_at = _now_iso()
            self.agents_state[agent_name]["current_file"] = best_path
            self._write()

            return best_file

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
            f.retry_count += 1
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
