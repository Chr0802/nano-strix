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

# Make sibling packages and nano_strix importable -- per_file.py lives in agents/
# When launched as a subprocess, the package may not be on PYTHONPATH.
_HERE = Path(__file__).resolve().parent
_PROJECT_SRC = _HERE.parent.parent  # src/nano_strix/agents/ -> src/
for _p in (str(_HERE), str(_PROJECT_SRC)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from per_file_lib.classifier import classify_files  # noqa: E402
from per_file_lib.scanner import run_static_scans  # noqa: E402
from per_file_lib.sub_agents import SubAgentRunner  # noqa: E402

logger = logging.getLogger(__name__)


def _setup_logging(workspace: Path) -> None:
    """Configure logging for this subprocess using shared setup utility."""
    try:
        from nano_strix.config.loader import load_config
        from nano_strix.config.paths import DEFAULT_CONFIG_PATH
        from nano_strix.logging.setup import setup_logging

        cfg = load_config(DEFAULT_CONFIG_PATH)
        log_file = workspace / "per_file.log"
        setup_logging(cfg.logging, log_file=log_file)
    except Exception:
        # Fallback: basic stderr logging
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
            datefmt="%H:%M:%S",
            stream=sys.stderr,
        )
        logger.warning("Could not load logging config, using fallback", exc_info=True)


def load_config_from_workspace(workspace: Path) -> dict:
    """Load per_file config from workspace or use defaults."""
    config_path = workspace / "config.yaml"
    if config_path.exists():
        import yaml

        with open(config_path) as f:
            return yaml.safe_load(f).get("per_file", {})
    return {}


def create_llm_client(model_name: str, config: dict):
    """Create an LLM client for the given model.

    Reads api_key and base_url from the global nano-strix config file
    (``~/.nano-strix/config.yaml``), falling back to environment variables
    when the config file is absent or incomplete.
    """
    try:
        from nano_strix.config.loader import load_config
        from nano_strix.config.paths import DEFAULT_CONFIG_PATH
        from nano_strix.config.schema import LLMConfig
        from nano_strix.llm.factory import create_provider

        global_cfg = load_config(DEFAULT_CONFIG_PATH)
        llm_cfg = LLMConfig(
            provider=global_cfg.llm.provider,
            api_key=global_cfg.llm.api_key,
            base_url=global_cfg.llm.base_url,
            model=model_name,
        )
        return create_provider(llm_cfg)
    except Exception:
        logger.warning("Could not create LLM provider via factory, using environment")
        import os

        try:
            from nano_strix.llm.anthropic import AnthropicProvider

            return AnthropicProvider(
                api_key=os.environ.get("ANTHROPIC_API_KEY", ""),
                base_url=os.environ.get("ANTHROPIC_BASE_URL", ""),
                model=model_name,
            )
        except Exception:
            raise RuntimeError(
                "No LLM provider available. "
                "Set ANTHROPIC_API_KEY or configure llm in config.yaml"
            )


async def main_async() -> None:
    """Main async entry point."""
    t_run_start = _time.monotonic()

    # Read task from stdin
    line = sys.stdin.readline()
    if not line:
        logger.error("No input on stdin")
        sys.exit(1)

    msg = json.loads(line)
    task_id = msg["task_id"]
    target = msg.get("payload", {}).get("target", ".")

    target_path = Path(target)
    if not target_path.exists():
        result = {
            "type": "result",
            "task_id": task_id,
            "payload": {"status": "error", "error": f"Target not found: {target}"},
        }
        print(json.dumps(result))
        return

    # Determine workspace
    workspace = target_path.parent  # workspace/{task_id}/
    manifest_path = workspace / "file_manifest.json"

    # Set up logging (after workspace is known so log file can be placed there)
    _setup_logging(workspace)

    logger.info("=" * 60)
    logger.info("per_file agent starting: task_id=%s target=%s", task_id, target)
    logger.info("=" * 60)

    # Load config
    config = load_config_from_workspace(workspace)

    agent_names = [
        "route_agent",
        "dataflow_agent",
        "auth_agent",
        "dependency_agent",
    ]

    phase_timings: dict[str, float] = {}

    try:
        # Determine default model from global config
        default_model = "claude-sonnet-4-6"
        try:
            from nano_strix.config.loader import load_config
            from nano_strix.config.paths import DEFAULT_CONFIG_PATH

            global_cfg = load_config(DEFAULT_CONFIG_PATH)
            if global_cfg.llm.model:
                default_model = global_cfg.llm.model
        except Exception:
            pass

        logger.info("Default model: %s", default_model)
        logger.info(
            "Config: cls_model=%s analysis_model=%s "
            "max_concurrent=%s scanners=%s",
            config.get("classification_model", default_model),
            config.get("analysis_model", default_model),
            config.get("max_concurrent", 4),
            config.get("static_scanners", ["semgrep", "bandit"]),
        )

        # ---- Phase 1: Classification ----
        logger.info("--- Phase 1: Classification ---")
        t_phase1 = _time.monotonic()
        classification_model = config.get("classification_model", default_model)
        classifier_client = create_llm_client(classification_model, config)

        manifest = await classify_files(
            target_dir=str(target_path),
            manifest_path=manifest_path,
            llm_client=classifier_client,
            agent_names=agent_names,
            max_file_retries=config.get("max_file_retries", 3),
        )
        phase_timings["phase1_classification"] = round(_time.monotonic() - t_phase1, 3)

        _emit_progress(
            task_id,
            "phase1_complete",
            {
                "total_files": len(manifest.files),
                "elapsed_s": phase_timings["phase1_classification"],
            },
        )

        # ---- Phase 2: Static scanning ----
        logger.info("--- Phase 2: Static Scanning ---")
        t_phase2 = _time.monotonic()
        scanners = config.get("static_scanners", ["semgrep", "bandit"])
        await run_static_scans(
            manifest=manifest,
            target_dir=str(target_path),
            scanners=scanners,
        )
        phase_timings["phase2_static_scan"] = round(_time.monotonic() - t_phase2, 3)

        _emit_progress(
            task_id,
            "phase2_complete",
            {
                "total_files": len(manifest.files),
                "elapsed_s": phase_timings["phase2_static_scan"],
            },
        )

        # ---- Phase 3: Multi-agent parallel analysis ----
        logger.info("--- Phase 3: Multi-Agent Analysis ---")
        t_phase3 = _time.monotonic()
        manifest.phase = "analysis"
        manifest.save()

        analysis_model = config.get("analysis_model", default_model)
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
        health_interval = config.get("health_check_interval_seconds", 30)

        def _health_check_loop():
            while not runner._stop_event.is_set():
                _time.sleep(health_interval)
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

        phase_timings["phase3_analysis"] = round(_time.monotonic() - t_phase3, 3)

        # ---- Collect results ----
        all_findings = []
        for f in manifest.files.values():
            all_findings.extend(f.findings)

        coverage = manifest._compute_coverage()

        total_elapsed = _time.monotonic() - t_run_start
        logger.info("=" * 60)
        logger.info(
            "per_file agent complete: %d files, %d findings in %.1fs",
            coverage["total"], len(all_findings), total_elapsed,
        )
        logger.info(
            "Phase timings: P1=%.1fs P2=%.1fs P3=%.1fs",
            phase_timings.get("phase1_classification", 0),
            phase_timings.get("phase2_static_scan", 0),
            phase_timings.get("phase3_analysis", 0),
        )
        logger.info("=" * 60)

        # Save run metadata
        run_meta = {
            "task_id": task_id,
            "target": target,
            "timings": phase_timings,
            "total_elapsed_s": round(total_elapsed, 3),
            "model": default_model,
            "coverage": coverage,
            "findings_count": len(all_findings),
        }
        (workspace / "per_file_run_meta.json").write_text(
            json.dumps(run_meta, indent=2, ensure_ascii=False)
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
                "timings": phase_timings,
            },
        }
        print(json.dumps(result, ensure_ascii=False))

    except Exception as exc:
        total_elapsed = _time.monotonic() - t_run_start
        logger.exception("per_file agent failed after %.1fs", total_elapsed)
        error_result = {
            "type": "result",
            "task_id": task_id,
            "payload": {
                "status": "error",
                "error": str(exc),
                "elapsed_s": round(total_elapsed, 3),
            },
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
