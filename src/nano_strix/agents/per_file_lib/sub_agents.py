# src/nano_strix/agents/per_file_lib/sub_agents.py
from __future__ import annotations

import asyncio
import json as _json
import logging
import sys
import threading
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from nano_strix.agents.per_file_lib.manifest import FileManifest

logger = logging.getLogger(__name__)

AGENT_SYSTEM_PROMPTS: dict[str, str] = {
    "route_agent": (
        "You are a Route Discovery agent. Your task is to find all HTTP/API entry "
        "points in the given source file. Identify Flask routes, FastAPI endpoints, "
        "Express routers, Django URL patterns, etc. For each route found, record: "
        "path, HTTP method, file location, line number, and handler function name.\n"
        "If the file contains NO routes, respond with an empty findings list."
    ),
    "dataflow_agent": (
        "You are a Dataflow Analysis agent. Trace user input from source to dangerous "
        "sink in the given source file. Identify: sources (request parameters, user "
        "input, file uploads), transformations (validation, sanitization, encoding), "
        "and sinks (SQL queries, command execution, file operations, deserialization, "
        "template rendering).\n"
        "Flag any missing input validation or sanitization as a finding.\n"
        "If the file contains NO dataflow concerns, respond with an empty "
        "findings list."
    ),
    "auth_agent": (
        "You are an Authentication/Authorization agent. Analyze the given source file "
        "for: authentication mechanisms, session management, JWT handling, password "
        "hashing, authorization checks, permission middleware, OAuth flows, API key "
        "validation.\n"
        "Flag: missing auth checks, weak crypto, hardcoded credentials, insecure "
        "session config.\n"
        "If the file contains NO auth concerns, respond with an empty findings list."
    ),
    "dependency_agent": (
        "You are a Dependency Analysis agent. Analyze the given source file for "
        "third-party library usage and known vulnerabilities. Check: imported packages "
        "against CVE databases, dependency version constraints, deprecated libraries, "
        "license compliance.\n"
        "For dependency declaration files (requirements.txt, package.json, pom.xml, "
        "etc.), enumerate all dependencies and flag any with known vulnerabilities.\n"
        "If the file contains NO dependency concerns, respond with an empty findings "
        "list."
    ),
}


class SubAgentRunner:
    """Manages 4 parallel sub-agent threads with checkpoint and retry support."""

    def __init__(
        self,
        manifest: FileManifest,
        llm_client,
        semaphore: threading.Semaphore,
        target_dir: str,
        max_agent_restarts: int = 3,
        health_check_interval: int = 30,
    ) -> None:
        self._manifest = manifest
        self._llm_client = llm_client
        self._semaphore = semaphore
        self._target_dir = target_dir
        self._max_agent_restarts = max_agent_restarts
        self._health_check_interval = health_check_interval
        self._threads: dict[str, threading.Thread] = {}
        self._threads_lock = threading.Lock()
        self._max_iterations: int = 300
        self._stop_event = threading.Event()

    # ---- Public API ----

    def run_all(self, max_iterations: int = 300, phase3_timeout: int = 1800) -> None:
        """Spawn all sub-agent threads and wait for completion."""
        self._max_iterations = max_iterations
        agent_names = list(self._manifest.agents_state.keys())

        for name in agent_names:
            state = self._manifest.agents_state[name]
            if state["status"] in ("completed",):
                continue
            self._start_agent_thread(name, max_iterations)

        # Wait with timeout
        deadline = datetime.now().timestamp() + phase3_timeout
        with self._threads_lock:
            threads_snapshot = list(self._threads.items())
        for name, thread in threads_snapshot:
            remaining = deadline - datetime.now().timestamp()
            if remaining <= 0:
                logger.warning(
                    "Phase 3 timeout reached, remaining agents will be collected"
                )
                break
            thread.join(timeout=max(1, remaining))

        # Second pass: join any threads added by crash recovery
        with self._threads_lock:
            recovery_threads = [
                (name, t) for name, t in self._threads.items()
                if t.is_alive()
            ]
        for name, thread in recovery_threads:
            remaining = deadline - datetime.now().timestamp()
            if remaining <= 0:
                break
            thread.join(timeout=max(1, remaining))

        # Collect results from completed threads
        self._collect_results()

    def run_single_agent(self, agent_name: str, max_iterations: int = 300) -> None:
        """Run a single agent synchronously (for testing)."""
        self._max_iterations = max_iterations
        self._start_agent_thread(agent_name, max_iterations)
        # Keep joining until no more restarts occur (crash recovery spawns new threads)
        while True:
            with self._threads_lock:
                thread = self._threads.get(agent_name)
            if thread is None:
                break
            thread.join()
            # Check if the agent was restarted (crash handler replaced the thread)
            with self._threads_lock:
                new_thread = self._threads.get(agent_name)
            if new_thread is None or new_thread is thread:
                break

    # ---- Agent thread management ----

    def _start_agent_thread(self, agent_name: str, max_iterations: int) -> None:
        thread = threading.Thread(
            target=self._agent_thread_entry,
            args=(agent_name, max_iterations),
            daemon=True,
            name=f"per_file_{agent_name}",
        )
        with self._threads_lock:
            self._threads[agent_name] = thread
        self._manifest.update_agent_state(agent_name, {
            "status": "running",
            "thread_id": thread.ident,
            "last_health_check": datetime.now(timezone.utc).isoformat(),
        })
        thread.start()

    def _agent_thread_entry(self, agent_name: str, max_iterations: int) -> None:
        """Entry point for each agent thread."""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(
                self._agent_loop(agent_name, max_iterations)
            )
        except Exception:
            logger.error("Agent %s crashed:\n%s", agent_name, traceback.format_exc())
            self._handle_agent_crash(agent_name, exc_info=sys.exc_info())
        finally:
            loop.close()

    # ---- Agent loop ----

    async def _agent_loop(self, agent_name: str, max_iterations: int) -> None:
        """Core agent loop: claim file -> analyze -> update manifest -> repeat."""
        state = self._manifest.agents_state[agent_name]
        iteration = state.get("iteration", 0)
        system_prompt = AGENT_SYSTEM_PROMPTS.get(agent_name, "")
        my_dimension = {
            "route_agent": "route",
            "dataflow_agent": "dataflow",
            "auth_agent": "auth",
            "dependency_agent": "dependency",
        }.get(agent_name)

        while not self._manifest.can_finish() and iteration < max_iterations:
            if self._stop_event.is_set():
                break

            # Health heartbeat
            self._manifest.update_agent_state(agent_name, {
                "last_health_check": datetime.now(timezone.utc).isoformat(),
                "iteration": iteration,
            })

            target = self._manifest.claim_pending_file(agent_name)
            if target is None:
                self._manifest.vote_skip_remaining(
                    agent_name, reason="all matching files processed"
                )
                break

            file_path = target.path

            # Non-matching dimension -> vote skip, don't waste LLM call
            if my_dimension and my_dimension not in target.dimensions:
                reason_text = (
                    f"{agent_name}: dimension '{my_dimension}' not in "
                    f"file dimensions {target.dimensions}"
                )
                self._manifest.vote_skip(
                    file_path, agent_name, reason=reason_text
                )
                self._manifest.update_agent_state(agent_name, {
                    "files_skipped": self._manifest.agents_state[agent_name].get("files_skipped", 0) + 1
                })
                iteration += 1
                continue

            try:
                # Read file content
                full_path = Path(self._target_dir) / file_path
                try:
                    content = full_path.read_text(errors="replace")
                except Exception:
                    content = f"[Could not read file: {file_path}]"

                scan_results = target.scan_findings
                hints = self._manifest.get_hints(agent_name)

                # Build messages
                hint_text = ""
                if hints.get("discovered_routes"):
                    hint_text = "\n\nDiscovered routes from route analysis:\n" + \
                        "\n".join(
                            f"  {r['method']} {r['path']} ({r['file']}:{r['line']})"
                            for r in hints["discovered_routes"]
                        )

                user_prompt = (
                    f"File: {file_path}\n"
                    f"Priority: {target.priority}\n"
                    f"Static scan findings: {scan_results}\n"
                    f"{hint_text}\n\n"
                    f"Source code:\n```\n{content[:8000]}\n```\n\n"
                    "Return a JSON object with a 'findings' list. "
                    "Each finding should have: id, title, severity "
                    "(critical/high/medium/low/info), category, "
                    "file_path, line_range [start, end], description, "
                    "code_snippet, recommendation, confidence (0-1)."
                )

                self._semaphore.acquire()
                try:
                    response = await self._llm_client.chat(
                        messages=[
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_prompt},
                        ],
                        temperature=0.1,
                        max_tokens=4096,
                    )
                finally:
                    self._semaphore.release()

                # Parse response
                findings = self._parse_findings(response.content or "", file_path)

                # Mark as analyzed
                self._manifest.update_file(
                    file_path, findings=findings, status="analyzed"
                )
                # Cast analyze vote
                target.skip_votes[agent_name] = "analyze"
                self._manifest.save()

                self._manifest.update_agent_state(agent_name, {
                    "files_analyzed": self._manifest.agents_state[agent_name].get("files_analyzed", 0) + 1
                })

                # If route_agent, extract discovered routes
                if agent_name == "route_agent":
                    self._extract_routes(findings, file_path)

            except Exception:
                logger.exception("Agent %s error on file %s", agent_name, file_path)
                self._manifest.handle_agent_error(file_path, agent_name)
                raise  # Propagate to thread entry for crash/restart handling

            iteration += 1

        # Agent finished
        if iteration >= max_iterations:
            self._manifest.vote_skip_remaining(
                agent_name, reason="max_iterations reached"
            )

        self._manifest.update_agent_state(agent_name, {
            "status": "completed",
            "iteration": iteration,
        })

    # ---- Helpers ----

    def _parse_findings(self, content: str, file_path: str) -> list[dict[str, Any]]:
        raw = content.strip()
        if raw.startswith("```"):
            lines = raw.split("\n")
            raw = "\n".join(lines[1:])
        if raw.endswith("```"):
            raw = raw[:-3].strip()
        try:
            data = _json.loads(raw)
            findings = data.get("findings", [])
        except _json.JSONDecodeError:
            logger.warning("Could not parse findings JSON for %s", file_path)
            return []

        for f in findings:
            f.setdefault("file_path", file_path)
        return findings

    def _extract_routes(
        self, findings: list[dict[str, Any]], file_path: str
    ) -> None:
        for f in findings:
            route_info = f.get("route")
            if route_info and isinstance(route_info, dict):
                self._manifest.add_discovered_route({
                    "path": route_info.get("path", ""),
                    "method": route_info.get("method", "GET"),
                    "file": file_path,
                    "line": route_info.get("line", 0),
                })

    def _handle_agent_crash(self, agent_name: str, exc_info=None) -> None:
        """Handle agent thread crash: clean orphan files, restart if possible."""
        state = self._manifest.agents_state[agent_name]
        restart_count = state.get("restart_count", 0)

        # Clean up orphan files
        for path, f in self._manifest.files.items():
            if f.assigned_to == agent_name and f.status == "analyzing":
                self._manifest.handle_agent_error(path, agent_name)

        # Build crash reason safely (format_exc returns None when no active exception)
        if exc_info is not None:
            crash_reason = "".join(traceback.format_exception(*exc_info))[-500:]
        else:
            fb = traceback.format_exc()
            crash_reason = (fb or "")[-500:]

        if restart_count < self._max_agent_restarts:
            self._manifest.update_agent_state(agent_name, {
                "status": "restarted",
                "restart_count": restart_count + 1,
                "current_file": None,
                "crash_reason": crash_reason,
            })
            logger.warning("Restarting %s (attempt %d/%d)",
                           agent_name, restart_count + 1, self._max_agent_restarts)
            self._start_agent_thread(agent_name, self._max_iterations)
        else:
            self._manifest.update_agent_state(agent_name, {
                "status": "crashed",
                "crash_reason": f"max restarts ({self._max_agent_restarts}) exceeded",
            })
            self._manifest.vote_skip_remaining(
                agent_name, reason=f"agent crashed after {restart_count} restarts"
            )

    def detect_unhealthy_agents(
        self, orphan_timeout_seconds: int = 600
    ) -> dict[str, str]:
        """Check for agents that haven't updated health check within timeout."""
        unhealthy = {}
        now = datetime.now(timezone.utc)
        for name, state in self._manifest.agents_state.items():
            if state["status"] != "running":
                continue
            last = state.get("last_health_check")
            if not last:
                continue
            try:
                last_time = datetime.fromisoformat(last)
            except ValueError:
                continue
            if (now - last_time).total_seconds() > orphan_timeout_seconds:
                unhealthy[name] = f"last check at {last}"
        return unhealthy

    def _collect_results(self) -> None:
        """Collect results from completed threads."""
        with self._threads_lock:
            items = list(self._threads.items())
        for name, thread in items:
            if thread.is_alive():
                logger.warning("Agent %s still running at collection time", name)
