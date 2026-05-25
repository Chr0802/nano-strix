from __future__ import annotations

from pathlib import Path
from typing import Any

from nano_strix.logging.logger import JSONLLogger, LogEntry


class GraphLogger:
    def __init__(
        self,
        path: Path,
        task_id: str = "",
        stage: str = "deep_analysis",
    ) -> None:
        self._logger = JSONLLogger(path)
        self._task_id = task_id
        self._stage = stage

    def _write(self, event: str, data: dict[str, Any]) -> None:
        self._logger.write(
            LogEntry(
                task_id=self._task_id,
                stage=self._stage,
                category="graph",
                level="info",
                event=event,
                data=data,
            )
        )

    def log_agent_created(
        self,
        agent_id: str,
        parent_id: str | None,
        name: str,
        task: str,
    ) -> None:
        self._write(
            "agent_created",
            {
                "agent_id": agent_id,
                "parent_id": parent_id,
                "name": name,
                "task": task,
            },
        )

    def log_agent_status_change(
        self,
        agent_id: str,
        old_status: str,
        new_status: str,
        reason: str = "",
    ) -> None:
        self._write(
            "agent_status_change",
            {
                "agent_id": agent_id,
                "old_status": old_status,
                "new_status": new_status,
                "reason": reason,
            },
        )

    def log_message_sent(
        self,
        from_id: str,
        to_id: str,
        msg_id: str,
        msg_type: str,
        priority: str,
    ) -> None:
        self._write(
            "message_sent",
            {
                "from": from_id,
                "to": to_id,
                "msg_id": msg_id,
                "msg_type": msg_type,
                "priority": priority,
            },
        )

    def log_agent_finished(
        self,
        agent_id: str,
        success: bool,
        findings_count: int,
        result_summary: str,
    ) -> None:
        self._write(
            "agent_finished",
            {
                "agent_id": agent_id,
                "success": success,
                "findings_count": findings_count,
                "result_summary": result_summary,
            },
        )

    def log_graph_viewed(
        self,
        agent_id: str,
        node_count: int,
        edge_count: int,
    ) -> None:
        self._write(
            "graph_viewed",
            {
                "agent_id": agent_id,
                "node_count": node_count,
                "edge_count": edge_count,
            },
        )
