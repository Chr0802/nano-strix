"""strix-per-file agent: per-file static analysis. Not yet implemented."""

import json
import sys


def main():
    line = sys.stdin.readline()
    msg = json.loads(line)
    result = {
        "type": "result",
        "task_id": msg["task_id"],
        "payload": {"error": "per_file agent not yet implemented"},
    }
    print(json.dumps(result))


if __name__ == "__main__":
    main()
