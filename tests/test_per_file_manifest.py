# tests/test_per_file_manifest.py
import json
import pytest


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

        FileManifest.create(empty_manifest_path, sample_files, ["route_agent"])
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


class TestClaimAtomicReservation:
    def test_claim_pending_file_atomically_reserves(self, empty_manifest_path, sample_files):
        from nano_strix.agents.per_file_lib.manifest import FileManifest

        manifest = FileManifest.create(empty_manifest_path, sample_files,
                                       ["route_agent"])
        claimed = manifest.claim_pending_file("route_agent")

        assert claimed is not None
        assert claimed.status == "analyzing"
        assert claimed.assigned_to == "route_agent"
        assert claimed.analyzing_started_at is not None
        assert claimed.retry_count == 0
        assert manifest.agents_state["route_agent"]["current_file"] == "src/auth/login.py"


class TestAgentError:
    def test_increments_retry_and_resets_status(self, empty_manifest_path, sample_files):
        from nano_strix.agents.per_file_lib.manifest import FileManifest

        manifest = FileManifest.create(empty_manifest_path, sample_files,
                                       ["route_agent"])
        manifest.claim_pending_file("route_agent")
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
        manifest.claim_pending_file("route_agent")
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
        manifest.claim_pending_file("route_agent")
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
