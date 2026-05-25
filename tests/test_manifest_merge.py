import json
from pathlib import Path
from nano_strix.agents.deep_analysis_lib.manifest import FileManifest, ManifestFile


def _make_manifest(path: Path, files: dict) -> FileManifest:
    m = FileManifest(
        path=path, phase="analysis", files=files,
        agents_state={}, discovered_routes=[],
    )
    m._agent_names = []
    return m


def test_manifest_merge_new_files():
    parent = _make_manifest(Path("/tmp/m1.json"), {})
    child = _make_manifest(Path("/tmp/m2.json"), {
        "src/a.py": ManifestFile(priority="high", dimensions=["auth"], _path="src/a.py"),
    })
    parent.merge(child)
    assert "src/a.py" in parent.files
    assert parent.files["src/a.py"].priority == "high"


def test_manifest_merge_combines_findings():
    parent = _make_manifest(Path("/tmp/m1.json"), {
        "src/x.py": ManifestFile(priority="high", dimensions=["route"], _path="src/x.py"),
    })
    parent.files["src/x.py"].findings = [{"id": "F-1", "title": "old"}]

    child = _make_manifest(Path("/tmp/m2.json"), {
        "src/x.py": ManifestFile(priority="high", dimensions=["route"], _path="src/x.py"),
    })
    child.files["src/x.py"].findings = [{"id": "F-2", "title": "new"}]

    parent.merge(child)
    assert len(parent.files["src/x.py"].findings) == 2


def test_manifest_merge_combines_scan_findings():
    parent = _make_manifest(Path("/tmp/m1.json"), {
        "src/x.py": ManifestFile(priority="high", dimensions=[], _path="src/x.py"),
    })
    parent.files["src/x.py"].scan_findings = [{"rule": "r1"}]

    child = _make_manifest(Path("/tmp/m2.json"), {
        "src/x.py": ManifestFile(priority="high", dimensions=[], _path="src/x.py"),
    })
    child.files["src/x.py"].scan_findings = [{"rule": "r2"}]

    parent.merge(child)
    assert len(parent.files["src/x.py"].scan_findings) == 2


def test_manifest_merge_skip_votes():
    parent = _make_manifest(Path("/tmp/m1.json"), {
        "src/x.py": ManifestFile(priority="high", dimensions=[], _path="src/x.py"),
    })
    parent.files["src/x.py"].skip_votes = {"agent_a": "analyze"}

    child = _make_manifest(Path("/tmp/m2.json"), {
        "src/x.py": ManifestFile(priority="high", dimensions=[], _path="src/x.py"),
    })
    child.files["src/x.py"].skip_votes = {"agent_b": "skip"}

    parent.merge(child)
    assert parent.files["src/x.py"].skip_votes == {"agent_a": "analyze", "agent_b": "skip"}


def test_manifest_full_roundtrip():
    """to_dict() -> from_dict() -> to_dict() should be idempotent."""
    parent = _make_manifest(Path("/tmp/m1.json"), {
        "src/x.py": ManifestFile(priority="high", dimensions=["auth", "route"], _path="src/x.py"),
    })
    parent.files["src/x.py"].findings = [{"id": "F-1"}]
    parent.files["src/x.py"].scan_findings = [{"rule": "r1"}]
    parent.files["src/x.py"].skip_votes = {"a": "analyze"}
    parent.phase = "analysis"
    parent.discovered_routes = [{"path": "/api", "method": "GET"}]

    d = parent.to_dict()
    restored = FileManifest.from_dict(d)

    assert restored.phase == "analysis"
    assert "src/x.py" in restored.files
    assert restored.files["src/x.py"].priority == "high"
    assert len(restored.files["src/x.py"].findings) == 1
