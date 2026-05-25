#!/usr/bin/env python3
"""Deep Analysis Stage: root-agent-driven recursive file analysis.

Replaces per_file.py and cross_file.py with a unified stage that uses
a strix-style agent graph. The RootAgent orchestrates 5 phases:
classify → scan → analyze → cross-link → review.
"""

from __future__ import annotations

import asyncio
import json as _json
import logging
import sys
import time as _time
from pathlib import Path
from typing import Any

from nano_strix.agents.deep_analysis_lib.deep_agent import RootAgent
from nano_strix.agents.deep_analysis_lib.graph import (
    AgentState,
    create_agent,
    wait_for_message,
    send_message_to_agent,
    agent_finish,
    view_agent_graph,
    _agent_graph,
    _running_agents,
)
from nano_strix.agents.deep_analysis_lib.manifest import FileManifest
from nano_strix.config.loader import load_config
from nano_strix.config.paths import DEFAULT_CONFIG_PATH
from nano_strix.llm.factory import create_provider
from nano_strix.logging.setup import setup_logging
from nano_strix.logging.llm_logger import LLMLogger
from nano_strix.logging.tool_logger import ToolLogger
from nano_strix.logging.graph_logger import GraphLogger
from nano_strix.agents.deep_analysis_lib.graph import (
    set_graph_logger,
    set_llm_logger,
    set_tool_logger,
)

logger = logging.getLogger(__name__)

# Register graph tools for LLM access
from nano_strix.tools.registry import register_tool

create_agent_tool = register_tool(create_agent)
send_message_tool = register_tool(send_message_to_agent)
wait_message_tool = register_tool(wait_for_message)
agent_finish_tool = register_tool(agent_finish)
view_graph_tool = register_tool(view_agent_graph)


def parse_ipc_input(raw: str) -> tuple[str, dict[str, Any]]:
    data = _json.loads(raw)
    return data["task_id"], data.get("payload", {})


def build_ipc_response(task_id: str, status: str, extra: dict[str, Any]) -> str:
    return _json.dumps({
        "type": "result",
        "task_id": task_id,
        "payload": {
            "status": status,
            "stage": "deep_analysis",
            **extra,
        },
    }, ensure_ascii=False)


def main() -> None:
    raw_input = sys.stdin.readline().strip()
    if not raw_input:
        response = build_ipc_response("unknown", "error", {"error": "No input received"})
        sys.stdout.write(response + "\n")
        sys.stdout.flush()
        return

    task_id, payload = parse_ipc_input(raw_input)
    target = payload.get("target", "")

    # Setup logging
    config = load_config(DEFAULT_CONFIG_PATH)
    setup_logging(config.logging)

    # Create structured JSONL loggers for this task
    logs_dir = Path(payload.get("workspace", ".")) / "logs"
    llm_logger = LLMLogger(logs_dir / "llm.jsonl")
    tool_logger = ToolLogger(logs_dir / "tools.jsonl")
    graph_logger = GraphLogger(logs_dir / "graph.jsonl", task_id=task_id)
    set_graph_logger(graph_logger)
    set_llm_logger(llm_logger)
    set_tool_logger(tool_logger)

    logger.info("Deep analysis stage started: task=%s target=%s", task_id, target)

    t_start = _time.monotonic()
    timings: dict[str, float] = {}

    try:
        # Create LLM provider
        llm = create_provider(config.llm)

        # Initialize skills
        from nano_strix.skills.loader import SkillLoader, set_skill_loader
        skills_dir = Path(__file__).parent.parent / "skills"
        skill_loader = SkillLoader(skills_dir)
        skill_loader.load_all()
        set_skill_loader(skill_loader)

        # Create RootAgent state
        root_state = AgentState(
            agent_name="DeepAnalysisRoot",
            task_id=task_id,
            task=f"Orchestrate deep analysis of target: {target}",
            role="root",
            max_iterations=500,
            waiting_timeout=1800,  # 30 min per phase
        )

        root_agent = RootAgent(
            state=root_state,
            llm_provider=llm,
            llm_logger=llm_logger,
            tool_logger=tool_logger,
        )

        # Emit root agent creation event (child agents are logged by create_agent)
        graph_logger.log_agent_created(
            agent_id=root_state.agent_id,
            parent_id=None,
            name=root_state.agent_name,
            task=root_state.task,
        )

        # Run root agent (synchronous wrapper for the async agent_loop)
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            result = loop.run_until_complete(root_agent.agent_loop())
        finally:
            loop.close()

        # Wait for any remaining sub-agents
        import threading
        for agent_id, thread in list(_running_agents.items()):
            thread.join(timeout=30)

        # Collect results from agent graph
        all_findings = []
        for node in _agent_graph["nodes"].values():
            node_result = node.get("result", {}) or {}
            node_findings = node_result.get("findings", [])
            if isinstance(node_findings, list):
                all_findings.extend(node_findings)

        timings["total"] = _time.monotonic() - t_start

        logger.info("Deep analysis complete: %d findings in %.1fs", len(all_findings), timings["total"])

        response = build_ipc_response(task_id, "ok", {
            "target": target,
            "findings": all_findings,
            "coverage_summary": {},
            "timings": timings,
        })

    except Exception as e:
        logger.exception("Deep analysis failed")
        timings["total"] = _time.monotonic() - t_start
        response = build_ipc_response(task_id, "error", {
            "target": target,
            "error": str(e),
            "timings": timings,
        })

    sys.stdout.write(response + "\n")
    sys.stdout.flush()


if __name__ == "__main__":
    main()
