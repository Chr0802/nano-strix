from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Finding:
    id: str
    title: str
    severity: str  # critical / high / medium / low / info
    category: str  # sql_injection / xss / rce / ...
    file_path: str
    line_range: tuple[int, int]
    description: str
    code_snippet: str
    recommendation: str
    confidence: float  # 0-1
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "severity": self.severity,
            "category": self.category,
            "file_path": self.file_path,
            "line_range": list(self.line_range),
            "description": self.description,
            "code_snippet": self.code_snippet,
            "recommendation": self.recommendation,
            "confidence": self.confidence,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Finding:
        lr = data["line_range"]
        return cls(
            id=data["id"],
            title=data["title"],
            severity=data["severity"],
            category=data["category"],
            file_path=data["file_path"],
            line_range=(lr[0], lr[1]),
            description=data["description"],
            code_snippet=data["code_snippet"],
            recommendation=data["recommendation"],
            confidence=data["confidence"],
            metadata=data.get("metadata", {}),
        )


@dataclass
class ExploitResult:
    finding_id: str
    verified: bool
    poc_script: str
    output: str
    exit_code: int
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "finding_id": self.finding_id,
            "verified": self.verified,
            "poc_script": self.poc_script,
            "output": self.output,
            "exit_code": self.exit_code,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ExploitResult:
        return cls(
            finding_id=data["finding_id"],
            verified=data["verified"],
            poc_script=data["poc_script"],
            output=data["output"],
            exit_code=data["exit_code"],
            metadata=data.get("metadata", {}),
        )
