from __future__ import annotations

import asyncio
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


# ---- AgentState ----

@dataclass
class AgentState:
    agent_id: str = field(default_factory=lambda: f"agent_{uuid.uuid4().hex[:8]}")
    agent_name: str = "DeepAnalyseAgent"
    parent_id: str | None = None
    task: str = ""
    role: str = ""  # classify / scan / analyze / cross-link / review / root
    messages: list[dict[str, Any]] = field(default_factory=list)
    iteration: int = 0
    max_iterations: int = 300
    completed: bool = False
    stop_requested: bool = False
    waiting_for_input: bool = False
    waiting_start_time: str | None = None
    waiting_timeout: int = 600
    final_result: dict[str, Any] | None = None
    _wake_event: asyncio.Event = field(default_factory=asyncio.Event)

    def add_message(self, role: str, content: str) -> None:
        self.messages.append({"role": role, "content": content})
        if self.waiting_for_input:
            self._wake_event.set()

    def enter_waiting_state(self) -> None:
        self.waiting_for_input = True
        self.waiting_start_time = datetime.now(timezone.utc).isoformat()

    def resume_from_waiting(self) -> None:
        self.waiting_for_input = False
        self.waiting_start_time = None
        self._wake_event.set()

    async def wait_for_wake(self, timeout: float = 0.5) -> None:
        try:
            await asyncio.wait_for(self._wake_event.wait(), timeout=timeout)
            self._wake_event.clear()
        except asyncio.TimeoutError:
            pass

    def should_stop(self) -> bool:
        return self.stop_requested or self.completed or self.iteration >= self.max_iterations

    def increment_iteration(self) -> None:
        self.iteration += 1

    def get_conversation_history(self) -> list[dict[str, Any]]:
        return list(self.messages)

    def has_waiting_timeout(self) -> bool:
        if self.waiting_timeout == 0:
            return False
        if not self.waiting_for_input or not self.waiting_start_time:
            return False
        if self.stop_requested or self.completed or self.iteration >= self.max_iterations:
            return False
        try:
            started = datetime.fromisoformat(self.waiting_start_time)
            elapsed = (datetime.now(timezone.utc) - started).total_seconds()
            return elapsed > self.waiting_timeout
        except ValueError:
            return False


# ---- Global State ----

_agent_graph: dict[str, Any] = {"nodes": {}, "edges": []}
_root_agent_id: str | None = None
_agent_messages: dict[str, list[dict[str, Any]]] = {}
_running_agents: dict[str, threading.Thread] = {}
_agent_instances: dict[str, Any] = {}
_agent_states: dict[str, Any] = {}
_agent_graph_lock = threading.Lock()


# ---- Helpers ----

def _generate_message_id() -> str:
    return f"msg_{uuid.uuid4().hex[:8]}"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
