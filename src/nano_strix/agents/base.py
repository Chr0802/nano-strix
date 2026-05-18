from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class IPCMessage:
    type: str  # task / progress / result / error / cancel
    task_id: str
    payload: dict[str, Any] = field(default_factory=dict)
    detail: str | None = None
    stage: str | None = None

    def to_json(self) -> str:
        data: dict[str, Any] = {"type": self.type, "task_id": self.task_id}
        if self.payload:
            data["payload"] = self.payload
        if self.detail:
            data["detail"] = self.detail
        if self.stage:
            data["stage"] = self.stage
        return json.dumps(data, ensure_ascii=False)

    @classmethod
    def from_json(cls, line: str) -> IPCMessage:
        data = json.loads(line)
        return cls(
            type=data["type"],
            task_id=data["task_id"],
            payload=data.get("payload", {}),
            detail=data.get("detail"),
            stage=data.get("stage"),
        )


class BaseAgent(ABC):
    @abstractmethod
    async def run(self, task_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Execute the agent's task and return results."""
        ...
