import json
from nano_strix.agents.deep_analysis import build_ipc_response, parse_ipc_input


def test_parse_ipc_input():
    msg = json.dumps({
        "type": "task", "task_id": "t-test",
        "stage": "deep_analysis",
        "payload": {"target": "/tmp/target", "stage_results": {}},
    })
    task_id, payload = parse_ipc_input(msg)
    assert task_id == "t-test"
    assert payload["target"] == "/tmp/target"


def test_build_ipc_response_success():
    resp = build_ipc_response("t-test", "ok", {
        "findings": [], "coverage_summary": {}, "timings": {},
    })
    data = json.loads(resp)
    assert data["type"] == "result"
    assert data["payload"]["status"] == "ok"


def test_build_ipc_response_error():
    resp = build_ipc_response("t-test", "error", {"error": "something broke"})
    data = json.loads(resp)
    assert data["payload"]["status"] == "error"
