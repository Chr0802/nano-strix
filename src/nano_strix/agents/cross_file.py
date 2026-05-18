"""strix-cross-file agent: cross-file analysis. Not yet implemented."""

import json
import sys


def main():
    line = sys.stdin.readline()
    msg = json.loads(line)
    result = {
        "type": "result",
        "task_id": msg["task_id"],
        "payload": {"error": "cross_file agent not yet implemented"},
    }
    print(json.dumps(result))


if __name__ == "__main__":
    main()
