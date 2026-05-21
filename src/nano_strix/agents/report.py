"""strix-report agent: findings aggregation and report generation.

Launched by AgentManager as a subprocess. Reads task JSON from stdin,
generates a final report, writes result JSON to stdout.
"""
from __future__ import annotations

import json
import logging
import sys
import time
from pathlib import Path

logger = logging.getLogger(__name__)

SLEEP_SECONDS = 1


def _setup_logging(workspace: Path | None = None) -> None:
    """Configure logging for this subprocess."""
    try:
        from nano_strix.config.loader import load_config
        from nano_strix.config.paths import DEFAULT_CONFIG_PATH
        from nano_strix.logging.setup import setup_logging

        cfg = load_config(DEFAULT_CONFIG_PATH)
        log_file = workspace / "report.log" if workspace else None
        setup_logging(cfg.logging, log_file=log_file)
    except Exception:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
            datefmt="%H:%M:%S",
            stream=sys.stderr,
        )


def main():
    line = sys.stdin.readline()
    msg = json.loads(line)
    task_id = msg["task_id"]
    payload = msg.get("payload", {})
    target = payload.get("target", "unknown")

    workspace_str = payload.get("workspace")
    workspace = Path(workspace_str) if workspace_str else None
    _setup_logging(workspace)

    logger.info("Task %s: start processing %s", task_id, target)
    logger.info("Task %s: workspace=%s", task_id, workspace)
    time.sleep(SLEEP_SECONDS)
    logger.info("Task %s: done", task_id)

    result = {
        "type": "result",
        "task_id": task_id,
        "payload": {
            "status": "ok",
            "report": "",
            "stage": "report",
            "target": target,
        },
    }
    print(json.dumps(result))


if __name__ == "__main__":
    main()
