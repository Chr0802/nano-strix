from nano_strix.shared.models import Finding, ExploitResult


def test_finding_creation():
    f = Finding(
        id="f-001",
        title="SQL Injection in login",
        severity="critical",
        category="sql_injection",
        file_path="src/auth.py",
        line_range=(42, 58),
        description="User input directly interpolated into SQL query",
        code_snippet="query = f\"SELECT * FROM users WHERE id={user_id}\"",
        recommendation="Use parameterized queries",
        confidence=0.95,
    )
    assert f.id == "f-001"
    assert f.severity == "critical"
    assert f.confidence == 0.95


def test_finding_to_dict():
    f = Finding(
        id="f-001",
        title="XSS",
        severity="high",
        category="xss",
        file_path="src/views.py",
        line_range=(10, 20),
        description="Reflected XSS",
        code_snippet="echo(user_input)",
        recommendation="Sanitize output",
        confidence=0.8,
    )
    d = f.to_dict()
    assert d["id"] == "f-001"
    assert d["severity"] == "high"
    assert isinstance(d, dict)


def test_finding_from_dict():
    data = {
        "id": "f-001",
        "title": "XSS",
        "severity": "high",
        "category": "xss",
        "file_path": "src/views.py",
        "line_range": [10, 20],
        "description": "Reflected XSS",
        "code_snippet": "echo(user_input)",
        "recommendation": "Sanitize output",
        "confidence": 0.8,
        "metadata": {},
    }
    f = Finding.from_dict(data)
    assert f.id == "f-001"
    assert f.line_range == (10, 20)


def test_exploit_result_creation():
    r = ExploitResult(
        finding_id="f-001",
        verified=True,
        poc_script="poc_auth_sqli.py",
        output="Successfully extracted admin password",
        exit_code=0,
    )
    assert r.verified is True
    assert r.finding_id == "f-001"


def test_exploit_result_to_dict():
    r = ExploitResult(
        finding_id="f-001",
        verified=True,
        poc_script="poc.py",
        output="ok",
        exit_code=0,
    )
    d = r.to_dict()
    assert d["verified"] is True
