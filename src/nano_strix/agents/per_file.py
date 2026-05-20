# src/nano_strix/agents/per_file.py
"""per_file agent: multi-phase LLM-driven file-by-file security analysis.

Launched by AgentManager as a subprocess. Reads task JSON from stdin,
runs 3-phase analysis, writes result JSON to stdout.
"""
from __future__ import annotations

import asyncio
import json
import logging
import sys
import threading
import time as _time
from pathlib import Path

# Make sibling packages and nano_strix importable — per_file.py lives in agents/
# When launched as a subprocess, the package may not be on PYTHONPATH.
_HERE = Path(__file__).resolve().parent
_PROJECT_SRC = _HERE.parent.parent  # src/nano_strix/agents/ -> src/
for _p in (str(_HERE), str(_PROJECT_SRC)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from per_file_lib.manifest import FileManifest  # noqa: E402
from per_file_lib.classifier import classify_files  # noqa: E402
from per_file_lib.scanner import run_static_scans  # noqa: E402
from per_file_lib.sub_agents import SubAgentRunner  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [per_file] %(message)s")
logger = logging.getLogger(__name__)


def load_config_from_workspace(workspace: Path) -> dict:
    """Load per_file config from workspace or use defaults."""
    config_path = workspace / "config.yaml"
    if config_path.exists():
        import yaml

        with open(config_path) as f:
            return yaml.safe_load(f).get("per_file", {})
    return {}


def create_llm_client(model_name: str, config: dict):
    """Create an LLM client for the given model. Tries to use project's factory."""
    try:
        from nano_strix.config.schema import LLMConfig
        from nano_strix.llm.factory import create_provider

        cfg = LLMConfig(model=model_name)
        return create_provider(cfg)
    except Exception:
        logger.warning("Could not create LLM provider via factory, using environment")
        import os

        try:
            from nano_strix.llm.anthropic import AnthropicProvider

            return AnthropicProvider(
                api_key=os.environ.get("ANTHROPIC_API_KEY", ""),
                model=model_name,
            )
        except Exception:
            raise RuntimeError(
                "No LLM provider available. Set ANTHROPIC_API_KEY or configure llm in config.yaml"
            )


async def main_async() -> None:
    """Main async entry point."""
    # Read task from stdin
    line = sys.stdin.readline()
    if not line:
        logger.error("No input on stdin")
        sys.exit(1)

    msg = json.loads(line)
    task_id = msg["task_id"]
    target = msg.get("payload", {}).get("target", ".")
    stage_results = msg.get("payload", {}).get("stage_results", {})

    logger.info("Task %s: starting per_file analysis of %s", task_id, target)

    target_path = Path(target)
    if not target_path.exists():
        result = {
            "type": "result",
            "task_id": task_id,
            "payload": {"status": "error", "error": f"Target not found: {target}"},
        }
        print(json.dumps(result))
        return

    # Determine workspace: parent of target (since target is typically workspace/{task_id}/source)
    # TODO: workspace discovery from target path parent is a convention;
    # ideally the workspace path should come from the task payload.
    workspace = target_path.parent  # workspace/{task_id}/
    manifest_path = workspace / "file_manifest.json"

    # Load config
    config = load_config_from_workspace(workspace)

    agent_names = [
        "route_agent",
        "dataflow_agent",
        "auth_agent",
        "dependency_agent",
    ]

    try:
        # Phase 1: Classification
        logger.info("Phase 1: Discovering and classifying files...")
        classification_model = config.get("classification_model", "claude-haiku-4-5-20251001")
        classifier_client = create_llm_client(classification_model, config)

        manifest = await classify_files(
            target_dir=str(target_path),
            manifest_path=manifest_path,
            llm_client=classifier_client,
            agent_names=agent_names,
            max_file_retries=config.get("max_file_retries", 3),
        )

        _emit_progress(
            task_id,
            "phase1_complete",
            {
                "total_files": len(manifest.files),
            },
        )

        # Phase 2: Static scanning
        logger.info("Phase 2: Running static scanners...")
        scanners = config.get("static_scanners", ["semgrep", "bandit"])
        await run_static_scans(
            manifest=manifest,
            target_dir=str(target_path),
            scanners=scanners,
        )

        _emit_progress(
            task_id,
            "phase2_complete",
            {
                "total_files": len(manifest.files),
            },
        )

        # Phase 3: Multi-agent parallel analysis
        logger.info("Phase 3: Starting parallel sub-agent analysis...")
        manifest.phase = "analysis"
        manifest.save()

        analysis_model = config.get("analysis_model", "claude-sonnet-4-6")
        analysis_client = create_llm_client(analysis_model, config)

        max_concurrent = config.get("max_concurrent", 4)
        llm_semaphore = threading.Semaphore(max_concurrent)

        runner = SubAgentRunner(
            manifest=manifest,
            llm_client=analysis_client,
            semaphore=llm_semaphore,
            target_dir=str(target_path),
            max_agent_restarts=config.get("max_agent_restarts", 3),
            health_check_interval=config.get("health_check_interval_seconds", 30),
        )

        # Start health check timer
        def _health_check_loop():
            while not runner._stop_event.is_set():
                _time.sleep(config.get("health_check_interval_seconds", 30))
                unhealthy = runner.detect_unhealthy_agents(
                    config.get("orphan_timeout_seconds", 600)
                )
                for name, reason in unhealthy.items():
                    logger.warning("Unhealthy agent %s: %s", name, reason)

        health_thread = threading.Thread(target=_health_check_loop, daemon=True)
        health_thread.start()

        try:
            runner.run_all(
                max_iterations=300,
                phase3_timeout=config.get("phase3_timeout_seconds", 1800),
            )
        finally:
            runner._stop_event.set()
            health_thread.join(timeout=5)

        # Collect findings
        all_findings = []
        for f in manifest.files.values():
            all_findings.extend(f.findings)

        coverage = manifest._compute_coverage()

        logger.info(
            "Analysis complete: %d files, %d findings",
            coverage["total"],
            len(all_findings),
        )

        result = {
            "type": "result",
            "task_id": task_id,
            "payload": {
                "status": "ok",
                "stage": "per_file",
                "target": target,
                "findings": all_findings,
                "coverage_summary": coverage,
                "manifest_path": str(manifest_path),
            },
        }
        print(json.dumps(result, ensure_ascii=False))

    except Exception as exc:
        logger.exception("per_file agent failed")
        error_result = {
            "type": "result",
            "task_id": task_id,
            "payload": {"status": "error", "error": str(exc)},
        }
        print(json.dumps(error_result, ensure_ascii=False))


def _emit_progress(task_id: str, phase: str, extra: dict) -> None:
    """Emit a progress message to stderr so stdout stays clean for IPC."""
    msg = {
        "type": "progress",
        "task_id": task_id,
        "payload": {"phase": phase, **extra},
    }
    sys.stderr.write(json.dumps(msg, ensure_ascii=False) + "\n")
    sys.stderr.flush()


def main():
    """Entry point for subprocess launch."""
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
