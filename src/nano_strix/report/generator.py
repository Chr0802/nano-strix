# src/nano_strix/report/generator.py
from __future__ import annotations

from nano_strix.shared.models import Finding, ExploitResult
from nano_strix.report.attack_graph import AttackGraph


class ReportGenerator:
    def generate(
        self,
        findings: list[Finding],
        target: str,
        exploit_results: list[ExploitResult] | None = None,
    ) -> str:
        sections = []
        sections.append(self._header(target))
        sections.append(self._executive_summary(findings))
        sections.append(self._findings_detail(findings, exploit_results))
        if len(findings) > 1:
            graph = AttackGraph(findings)
            sections.append(f"## 3. 攻击路径图\n\n{graph.to_mermaid()}\n")
        sections.append(self._fix_summary(findings))
        return "\n\n".join(sections)

    def _header(self, target: str) -> str:
        return f"# 渗透测试报告\n\n**目标:** {target}"

    def _executive_summary(self, findings: list[Finding]) -> str:
        severity_counts = {}
        for f in findings:
            severity_counts[f.severity] = severity_counts.get(f.severity, 0) + 1

        lines = ["## 1. 执行摘要\n", "| 严重程度 | 数量 |", "|----------|------|"]
        for sev in ["critical", "high", "medium", "low", "info"]:
            if sev in severity_counts:
                lines.append(f"| {sev} | {severity_counts[sev]} |")
        return "\n".join(lines)

    def _findings_detail(self, findings: list[Finding], exploit_results: list[ExploitResult] | None) -> str:
        verified_map = {}
        if exploit_results:
            for er in exploit_results:
                verified_map[er.finding_id] = er

        lines = ["## 2. 漏洞详情\n"]
        for f in findings:
            lines.append(f"### [{f.severity.upper()}] {f.title}")
            lines.append(f"- **文件:** `{f.file_path}:{f.line_range[0]}-{f.line_range[1]}`")
            lines.append(f"- **置信度:** {f.confidence}")
            lines.append(f"- **描述:** {f.description}")
            lines.append(f"- **代码片段:**\n```python\n{f.code_snippet}\n```")
            if f.id in verified_map:
                er = verified_map[f.id]
                status = "已验证" if er.verified else "未复现"
                lines.append(f"- **漏洞利用验证:** {status}")
                if er.output:
                    lines.append(f"- **验证输出:** {er.output}")
            lines.append(f"- **修复建议:** {f.recommendation}\n")
        return "\n".join(lines)

    def _fix_summary(self, findings: list[Finding]) -> str:
        lines = ["## 4. 修复建议汇总\n", "| 优先级 | 漏洞 | 修复建议 |", "|--------|------|----------|"]
        for i, f in enumerate(findings, 1):
            lines.append(f"| {i} | {f.title} | {f.recommendation} |")
        return "\n".join(lines)
