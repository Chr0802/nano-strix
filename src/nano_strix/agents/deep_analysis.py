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
    _agent_graph,
    _running_agents,
    set_graph_logger,
    set_llm_logger,
    set_tool_logger,
)
from nano_strix.agents.deep_analysis_lib.stage_state import reset_stage_state_manager
from nano_strix.config.loader import load_config
from nano_strix.config.paths import DEFAULT_CONFIG_PATH
from nano_strix.llm.factory import create_provider
from nano_strix.logging.graph_logger import GraphLogger
from nano_strix.logging.llm_logger import LLMLogger
from nano_strix.logging.setup import setup_logging
from nano_strix.logging.tool_logger import ToolLogger
from nano_strix.tools.context import set_current_sandbox, set_current_workspace_root

logger = logging.getLogger(__name__)


def _docker_is_available() -> bool:
    """Quick check that Docker daemon is reachable without blocking."""
    try:
        import docker
        client = docker.DockerClient(base_url="unix:///var/run/docker.sock", timeout=3)
        client.ping()
        client.close()
        return True
    except Exception:
        return False


async def _heartbeat_loop(root_state: Any) -> None:
    """Emit heartbeat lines to stdout while the agent loop is running.

    The heartbeat allows the orchestrator to detect that this subprocess
    is still making progress (not stuck), even when the agent loop runs
    for a long time.
    """
    HEARTBEAT_INTERVAL = 30  # seconds
    while True:
        await asyncio.sleep(HEARTBEAT_INTERVAL)
        try:
            from nano_strix.agents.deep_analysis_lib.graph import _agent_graph
            hb = _json.dumps({
                "type": "heartbeat",
                "ts": _time.time(),
                "iteration": root_state.iteration,
                "agent_count": len(_agent_graph["nodes"]),
            })
            sys.stdout.write(hb + "\n")
            sys.stdout.flush()
        except Exception:
            pass  # heartbeat must never crash the agent loop


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
    workspace = payload.get("workspace", "")

    # Restrict file operations to the task workspace
    workspace_root = workspace or target or "."
    set_current_workspace_root(str(Path(workspace_root).resolve()))
    logger.debug("Workspace root set to: %s", workspace_root)

    # Setup logging
    config = load_config(DEFAULT_CONFIG_PATH)
    setup_logging(config.logging)

    # Create structured JSONL loggers for this task
    logs_dir = Path(workspace or ".") / "logs"
    llm_logger = LLMLogger(logs_dir / "llm.jsonl")
    tool_logger = ToolLogger(logs_dir / "tools.jsonl")
    graph_logger = GraphLogger(logs_dir / "graph.jsonl", task_id=task_id)
    set_graph_logger(graph_logger)
    set_llm_logger(llm_logger)
    set_tool_logger(tool_logger)

    # Initialize harness stage state — contracts are auto-applied via hooks in graph.py
    reset_stage_state_manager()
    logger.debug("Harness initialized: stage state manager ready")

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

        # Run root agent inside Docker sandbox if configured and available
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        sandbox = None
        heartbeat_task: asyncio.Task | None = None
        try:
            if config.sandbox.sandbox_type == "docker":
                if _docker_is_available():
                    try:
                        from nano_strix.sandbox.docker import DockerSandbox
                        sandbox = DockerSandbox(
                            image=config.sandbox.image,
                            network=config.sandbox.network,
                            source_dir=Path(workspace_root),
                        )
                        loop.run_until_complete(
                            asyncio.wait_for(sandbox.create(), timeout=120)
                        )
                        set_current_sandbox(sandbox)
                        logger.info("Docker sandbox started on %s", sandbox.tool_server_url)
                    except asyncio.TimeoutError:
                        logger.warning(
                            "Docker sandbox creation timed out after 120s, "
                            "falling back to host tools"
                        )
                    except Exception:
                        logger.warning(
                            "Failed to create Docker sandbox, falling back to host tools",
                            exc_info=True,
                        )
                        sandbox = None
                else:
                    logger.info("Docker not available, using host tools directly")

            # Start heartbeat so the orchestrator can detect liveness
            heartbeat_task = loop.create_task(_heartbeat_loop(root_state))

            loop.run_until_complete(root_agent.agent_loop())
        finally:
            if heartbeat_task is not None:
                heartbeat_task.cancel()
            if sandbox:
                try:
                    loop.run_until_complete(sandbox.destroy())
                except Exception:
                    logger.warning("Failed to destroy sandbox", exc_info=True)
            loop.close()

        # Wait for any remaining sub-agents
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
