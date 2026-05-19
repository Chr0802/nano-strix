"""strix-report agent: 模拟执行，便于 CLI 调试。"""

import json
import logging
import sys
import time

logging.basicConfig(level=logging.INFO, format="%(asctime)s [report] %(message)s")
logger = logging.getLogger(__name__)

SLEEP_SECONDS = 1


def main():
    line = sys.stdin.readline()
    msg = json.loads(line)
    task_id = msg["task_id"]
    target = msg.get("payload", {}).get("target", "unknown")

    logger.info("Task %s: start processing %s", task_id, target)
    time.sleep(SLEEP_SECONDS)
    logger.info("Task %s: done", task_id)

    result = {
        "type": "result",
        "task_id": task_id,
        "payload": {
            "status": "ok",
            "report": [],
            "stage": "report",
            "target": target,
        },
    }
    print(json.dumps(result))


if __name__ == "__main__":
    main()
