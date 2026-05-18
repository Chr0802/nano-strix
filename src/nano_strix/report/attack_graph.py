# src/nano_strix/report/attack_graph.py
from __future__ import annotations

from nano_strix.shared.models import Finding


class AttackGraph:
    def __init__(self, findings: list[Finding]) -> None:
        self._findings = findings

    def to_mermaid(self) -> str:
        lines = ["graph TD"]
        for f in self._findings:
            node_id = f.id.replace("-", "_")
            severity_tag = f"[{f.severity.upper()}]"
            lines.append(f'    {node_id}["{severity_tag} {f.title}<br/>{f.file_path}:{f.line_range[0]}"]')

        # Chain findings by file dependency (simple heuristic)
        for i, f in enumerate(self._findings):
            node_id = f.id.replace("-", "_")
            if i > 0 and f.file_path != self._findings[i - 1].file_path:
                prev_id = self._findings[i - 1].id.replace("-", "_")
                lines.append(f"    {prev_id} -->|数据流| {node_id}")

        return "\n".join(lines)
