# tests/test_report_generator.py
from pathlib import Path

from nano_strix.report.attack_graph import AttackGraph
from nano_strix.report.generator import ReportGenerator
from nano_strix.shared.models import Finding


def test_report_generator_generate(tmp_path: Path):
    findings = [
        Finding(
            id="f-001",
            title="SQL Injection",
            severity="critical",
            category="sql_injection",
            file_path="src/auth.py",
            line_range=(42, 58),
            description="SQLi in login",
            code_snippet="query = f'SELECT * FROM users WHERE id={uid}'",
            recommendation="Use parameterized queries",
            confidence=0.95,
        ),
        Finding(
            id="f-002",
            title="XSS",
            severity="high",
            category="xss",
            file_path="src/views.py",
            line_range=(10, 20),
            description="Reflected XSS",
            code_snippet="echo(user_input)",
            recommendation="Sanitize output",
            confidence=0.8,
        ),
    ]

    gen = ReportGenerator()
    report = gen.generate(findings=findings, target="my-project")
    assert "# 渗透测试报告" in report
    assert "SQL Injection" in report
    assert "critical" in report


def test_attack_graph_build():
    findings = [
        Finding(
            id="f-001",
            title="SQLi",
            severity="critical",
            category="sql_injection",
            file_path="src/auth.py",
            line_range=(42, 58),
            description="SQLi",
            code_snippet="",
            recommendation="",
            confidence=0.9,
        ),
    ]
    graph = AttackGraph(findings)
    mermaid = graph.to_mermaid()
    assert "graph" in mermaid
    assert "f_001" in mermaid
