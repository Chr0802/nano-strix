"""Integration tests for the deep analysis stage full orchestration.

These tests script all LLM responses via a per-agent ScriptedMultiAgentLLM
to verify the full 5-phase pipeline (classify -> scan -> analyze -> cross-link
-> review) with real sub-agent threads, tool execution, and IPC output.

No real LLM calls or network access required.
"""

from __future__ import annotations

import io
import json
import threading

import pytest

from nano_strix.llm.adapter import LLMResponse, ToolCall


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _tc(name: str, args: dict) -> ToolCall:
    return ToolCall(id=f"tc_{name}", name=name, arguments=args)


def _resp(content: str | None = None, tool_calls: list[ToolCall] | None = None) -> LLMResponse:
    return LLMResponse(
        content=content,
        tool_calls=tool_calls or [],
        finish_reason="tool_calls" if tool_calls else "stop",
        usage={"input_tokens": 100, "output_tokens": 50},
        model="test-scripted-model",
    )


def _create_agent_response(name: str, task: str) -> LLMResponse:
    return _resp(tool_calls=[_tc("create_agent", {"name": name, "task": task})])


def _wait_for_message_response(reason: str) -> LLMResponse:
    return _resp(tool_calls=[_tc("wait_for_message", {"reason": reason})])


def _agent_finish_response(
    summary: str,
    findings: list[str] | None = None,
    report_to_parent: bool = True,
) -> LLMResponse:
    return _resp(tool_calls=[_tc("agent_finish", {
        "result_summary": summary,
        "findings": findings or [],
        "success": True,
        "report_to_parent": report_to_parent,
    })])


def _tool_call_response(tool_name: str, args: dict) -> LLMResponse:
    return _resp(tool_calls=[_tc(tool_name, args)])


def _mock_finding(fid: str, title: str, severity: str, file_path: str, line: int) -> str:
    return json.dumps({
        "id": fid,
        "title": title,
        "severity": severity,
        "exploitability": "E2",
        "nature": "C1",
        "category": "crypto",
        "file_path": file_path,
        "line_range": [line, line],
        "description": f"Mock finding: {title}",
        "code_snippet": "...",
        "recommendation": "Fix the issue",
        "confidence": "HIGH",
    })


# ---------------------------------------------------------------------------
# ScriptedMultiAgentLLM
# ---------------------------------------------------------------------------

class ScriptedMultiAgentLLM:
    """Fake LLM provider that returns per-agent scripted response sequences.

    Dispatches by ``agent_name`` (from the ContextVar set by
    ``set_current_agent_state`` in ``_process_iteration``). Thread-safe.
    """

    def __init__(self) -> None:
        self._sequences: dict[str, list[LLMResponse]] = {}
        self._call_history: list[dict] = []
        self._lock = threading.Lock()
        self.model = "test-scripted-model"

    def register_sequence(self, agent_name: str, responses: list[LLMResponse]) -> None:
        self._sequences[agent_name] = responses

    async def chat(
        self,
        messages: list | None = None,
        tools: list | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        from nano_strix.tools.context import get_current_agent_state

        state = get_current_agent_state()
        name = state.agent_name if state is not None else "unknown"

        with self._lock:
            idx = sum(1 for h in self._call_history if h["agent_name"] == name)
            tool_names = [t["name"] for t in (tools or [])]
            self._call_history.append({
                "agent_name": name,
                "call_index": idx,
                "tool_names": tool_names,
            })

        responses = self._sequences.get(name, [])
        if idx < len(responses):
            return responses[idx]

        # fallback: agent_finish to prevent infinite loop
        return _agent_finish_response("Fallback: no more scripted responses")

    async def stream_chat(
        self,
        messages: list | None = None,
        tools: list | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ):
        yield "{}"


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------

def _cleanup_globals() -> None:
    import nano_strix.agents.deep_analysis_lib.graph as g

    g._agent_graph["nodes"].clear()
    g._agent_graph["edges"].clear()
    g._root_agent_id = None
    g._agent_messages.clear()
    g._running_agents.clear()
    g._agent_instances.clear()
    g._agent_states.clear()

    # Also reset harness state
    from nano_strix.agents.deep_analysis_lib.hooks import clear_hooks
    from nano_strix.agents.deep_analysis_lib.stage_state import reset_stage_state_manager
    clear_hooks()
    reset_stage_state_manager()


@pytest.fixture(autouse=True)
def _reset_graph_globals():
    _cleanup_globals()
    yield
    _cleanup_globals()


def _build_target_app_dir(tmp_path) -> str:
    """Create a minimal Flask app structurally equivalent to llm-sec/app."""
    app_dir = tmp_path / "test_app"
    services_dir = app_dir / "services"
    utils_dir = app_dir / "utils"
    utils_dir.mkdir(parents=True)
    services_dir.mkdir(parents=True)

    (utils_dir / "__init__.py").write_text("")

    (app_dir / "__init__.py").write_text("""\
import os
from flask import Flask, request, abort, Response
from functools import wraps

def require_api_key(func):
    @wraps(func)
    def decorated_function(*args, **kwargs):
        api_key = request.headers.get('X-API-Key')
        expected_key = os.environ.get('API_KEY')
        if not expected_key:
            return Response('Server configuration error', status=500)
        if api_key != expected_key:
            abort(401, description='Unauthorized: Invalid API key')
        return func(*args, **kwargs)
    return decorated_function

class RemoveServerHeaderMiddleware:
    def __init__(self, app):
        self.app = app
    def __call__(self, environ, start_response):
        def custom_start_response(status, headers, exc_info=None):
            new_headers = [(k, v) for k, v in headers if k.lower() != 'server']
            return start_response(status, new_headers, exc_info)
        return self.app(environ, custom_start_response)

def create_app():
    app = Flask(__name__)
    app.config['SECRET_KEY'] = os.environ.get(
        'SECRET_KEY', 'default-secret-key-change-in-production'
    )
    @app.after_request
    def add_security_headers(response):
        response.headers['Content-Security-Policy'] = "default-src 'self'"
        response.headers['Strict-Transport-Security'] = 'max-age=31536000'
        response.headers['X-Frame-Options'] = 'DENY'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        return response
    from app.main import main_bp
    app.register_blueprint(main_bp)
    app.wsgi_app = RemoveServerHeaderMiddleware(app.wsgi_app)
    return app
""")

    (app_dir / "main.py").write_text("""\
from flask import Blueprint, request, jsonify
from app import require_api_key
from app.services.crypto_service import CryptoService

main_bp = Blueprint('main', __name__)
crypto_service = CryptoService()

@main_bp.route('/')
def index():
    return 'OK'

@main_bp.route('/api/key/exchange', methods=['POST'])
@require_api_key
def key_exchange():
    data = request.get_json()
    if not data or 'public_key' not in data:
        return jsonify({'error': 'Missing public key'}), 400
    result = crypto_service.exchange_keys(data['public_key'])
    return jsonify(result)

@main_bp.route('/api/key/verify', methods=['POST'])
@require_api_key
def key_verify():
    data = request.get_json()
    if not data or 'public_key' not in data:
        return jsonify({'error': 'Missing public key'}), 400
    result = crypto_service.verify_key(data['public_key'])
    return jsonify(result)

@main_bp.route('/api/message/encrypt', methods=['POST'])
@require_api_key
def encrypt_message():
    data = request.get_json()
    if not data or 'message' not in data or 'public_key' not in data:
        return jsonify({'error': 'Missing message or public key'}), 400
    result = crypto_service.encrypt_message(data['message'], data['public_key'])
    return jsonify(result)
""")

    (services_dir / "__init__.py").write_text("")
    (services_dir / "crypto_service.py").write_text("""\
import base64
import os
from cryptography.hazmat.primitives import serialization, hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.backends import default_backend

class CryptoService:
    def __init__(self):
        self.private_key = rsa.generate_private_key(
            public_exponent=65537, key_size=2048, backend=default_backend()
        )
        self.public_key = self.private_key.public_key()

    def get_server_public_key(self):
        return self.public_key.public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo
        ).decode('utf-8')

    def _deserialize_public_key(self, key_pem):
        try:
            return serialization.load_pem_public_key(
                key_pem.encode('utf-8'), backend=default_backend()
            )
        except Exception:
            return None

    def verify_key(self, public_key_pem):
        public_key = self._deserialize_public_key(public_key_pem)
        if public_key is None:
            return {'valid': False, 'message': 'Invalid key format'}
        try:
            public_key.public_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PublicFormat.SubjectPublicKeyInfo
            )
            return {'valid': True, 'message': 'Key verification passed'}
        except Exception as e:
            return {'valid': False, 'message': f'Key verification failed: {str(e)}'}

    def exchange_keys(self, public_key_pem):
        if public_key_pem == 'generate':
            return {'success': True, 'server_public_key': self.get_server_public_key()}
        public_key = self._deserialize_public_key(public_key_pem)
        if public_key is None:
            return {'success': False, 'error': 'Invalid public key'}
        shared_secret = os.urandom(32)
        try:
            encrypted_secret = public_key.encrypt(
                shared_secret,
                padding.OAEP(
                    mgf=padding.MGF1(algorithm=hashes.SHA256()),
                    algorithm=hashes.SHA256(), label=None
                )
            )
            return {
                'success': True,
                'encrypted_secret': base64.b64encode(encrypted_secret).decode('utf-8'),
                'server_public_key': self.get_server_public_key()
            }
        except Exception as e:
            return {'success': False, 'error': f'Key exchange failed: {str(e)}'}
""")

    return str(app_dir)


# ---------------------------------------------------------------------------
# monkeypatch helpers
# ---------------------------------------------------------------------------

def _build_test_config():
    from nano_strix.config.schema import (
        AppConfig,
        LLMConfig,
        SandboxConfig,
        LoggingConfig,
        PipelineConfig,
        IPCConfig,
        SchedulerConfig,
        PerFileConfig,
        DeepAnalysisConfig,
        SkillsConfig,
    )
    return AppConfig(
        llm=LLMConfig(provider="anthropic", api_key="test-key", model="test-model"),
        pipeline=PipelineConfig(),
        sandbox=SandboxConfig(sandbox_type="process"),
        ipc=IPCConfig(),
        logging=LoggingConfig(level="debug"),
        scheduler=SchedulerConfig(),
        per_file=PerFileConfig(),
        deep_analysis=DeepAnalysisConfig(),
        skills=SkillsConfig(),
    )


async def _noop_heartbeat(_root_state) -> None:
    return


# ---------------------------------------------------------------------------
# the integration test
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_full_five_phase_orchestration(tmp_path, monkeypatch):
    """Full 5-phase orchestration with scripted multi-agent LLM responses.

    Verifies the agent graph structure, per-agent LLM call history, and
    the final IPC response on stdout.
    """
    target_dir = _build_target_app_dir(tmp_path)

    # --- build ScriptedMultiAgentLLM ---
    fake_llm = ScriptedMultiAgentLLM()

    classify_task = "Classify all Python files in the target directory by priority and risk dimensions"
    scan_task = "Run static analysis tools on the target directory using the manifest from classification"
    analyze_task = "Perform deep per-file vulnerability analysis on all target files"
    crosslink_task = "Correlate findings across files to identify attack chains"
    review_task = "Review, deduplicate, and refine all findings; produce final deliverable"

    fake_llm.register_sequence("DeepAnalysisRoot", [
        _create_agent_response("FileClassifier", classify_task),
        _wait_for_message_response("Waiting for classification results"),
        _create_agent_response("StaticScanner", scan_task),
        _wait_for_message_response("Waiting for scan results"),
        _create_agent_response("PerFileAnalyzer", analyze_task),
        _wait_for_message_response("Waiting for per-file analysis results"),
        _create_agent_response("CrossLinkAnalyzer", crosslink_task),
        _wait_for_message_response("Waiting for cross-link analysis results"),
        _create_agent_response("ReviewRefiner", review_task),
        _wait_for_message_response("Waiting for review results"),
        _agent_finish_response("Deep analysis complete, all 5 phases finished",
                               report_to_parent=False),
    ])

    main_py = f"{target_dir}/main.py"
    crypto_py = f"{target_dir}/services/crypto_service.py"

    fake_llm.register_sequence("FileClassifier", [
        _tool_call_response("file_read", {"path": main_py, "max_lines": 200}),
        _tool_call_response("file_read", {"path": crypto_py, "max_lines": 200}),
        _agent_finish_response(
            "Classification complete. Found 3 Python files: "
            "main.py (high, route+dataflow), crypto_service.py (high, crypto), "
            "__init__.py (medium, auth)",
        ),
    ])

    fake_llm.register_sequence("StaticScanner", [
        _tool_call_response("file_read", {"path": main_py, "max_lines": 100}),
        _agent_finish_response(
            "Static scanning complete. Ran semgrep on target files, no critical issues.",
        ),
    ])

    f1 = _mock_finding("F-001", "Hardcoded secret key falls back to weak default",
                       "HIGH", "__init__.py", 33)
    f2 = _mock_finding("F-002", "No signature verification on received public keys",
                       "MEDIUM", "services/crypto_service.py", 23)

    fake_llm.register_sequence("PerFileAnalyzer", [
        _tool_call_response("file_read", {"path": main_py, "max_lines": 500}),
        _tool_call_response("file_read", {"path": crypto_py, "max_lines": 500}),
        _agent_finish_response(
            "Deep analysis complete. Found 2 vulnerabilities.",
            findings=[f1, f2],
        ),
    ])

    f3 = _mock_finding("F-003",
                       "Weak key default + unprotected key exchange enables MITM",
                       "CRITICAL", "__init__.py", 33)

    fake_llm.register_sequence("CrossLinkAnalyzer", [
        _tool_call_response("file_read", {"path": crypto_py, "max_lines": 200}),
        _agent_finish_response(
            "Cross-link analysis complete. Found 1 attack chain.",
            findings=[f3],
        ),
    ])

    fake_llm.register_sequence("ReviewRefiner", [
        _tool_call_response("file_read", {"path": crypto_py, "max_lines": 100}),
        _agent_finish_response(
            "Review complete. 3 findings validated, 0 duplicates removed, "
            "0 false positives.",
            findings=[f1, f2, f3],
        ),
    ])

    # --- monkeypatch deep_analysis module ---
    monkeypatch.setattr(
        "nano_strix.agents.deep_analysis.create_provider",
        lambda config: fake_llm,
    )
    monkeypatch.setattr(
        "nano_strix.agents.deep_analysis.load_config",
        lambda path: _build_test_config(),
    )
    monkeypatch.setattr(
        "nano_strix.agents.deep_analysis._docker_is_available",
        lambda: False,
    )
    monkeypatch.setattr(
        "nano_strix.agents.deep_analysis._heartbeat_loop",
        _noop_heartbeat,
    )

    # --- prepare stdin / stdout ---
    task_input = json.dumps({
        "type": "task",
        "task_id": "t-integration-deep",
        "stage": "deep_analysis",
        "payload": {
            "target": target_dir,
            "workspace": str(tmp_path),
            "stage_results": {},
        },
    })

    fake_stdin = io.StringIO(task_input + "\n")
    fake_stdout = io.StringIO()
    monkeypatch.setattr("sys.stdin", fake_stdin)
    monkeypatch.setattr("sys.stdout", fake_stdout)

    # Create stage result artifacts required by harness contract pre-hooks
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir(exist_ok=True)
    (tmp_path / "file_manifest.json").write_text(
        '{"files": {"main.py": {"status": "classified"}, '
        '"services/crypto_service.py": {"status": "classified"}}}'
    )
    for stage in ("scan", "analyze", "cross-link"):
        (logs_dir / f"stage_{stage}_result.json").write_text(
            '{"stage": "' + stage + '", "findings": []}'
        )

    # --- execute ---
    from nano_strix.agents.deep_analysis import main

    main()

    # ====================================================================
    # Phase A: graph structure assertions
    # ====================================================================
    from nano_strix.agents.deep_analysis_lib.graph import _agent_graph

    nodes = _agent_graph["nodes"]
    edges = _agent_graph["edges"]

    # 6+ nodes: root + 5 sub-agents
    assert len(nodes) >= 6, f"Expected >= 6 nodes, got {len(nodes)}: {list(nodes.keys())}"

    # root agent
    root_nodes = [n for n in nodes.values() if n.get("parent_id") is None]
    assert len(root_nodes) == 1, f"Expected 1 root, got {len(root_nodes)}"
    root_node = root_nodes[0]
    assert root_node["name"] == "DeepAnalysisRoot"
    assert root_node["role"] == "root"

    # 5 sub-agents with exact names
    expected_names = {
        "FileClassifier", "StaticScanner", "PerFileAnalyzer",
        "CrossLinkAnalyzer", "ReviewRefiner",
    }
    sub_agents = {nid: n for nid, n in nodes.items() if n.get("parent_id") is not None}
    sub_names = {n["name"] for n in sub_agents.values()}
    assert sub_names == expected_names, f"Sub-agent names: {sub_names}"

    # 10 delegation edges: each sub-agent gets an edge from create_agent AND
    # _register_in_graph (2 per sub-agent). This is a known duplication.
    delegation_edges = [e for e in edges if e["type"] == "delegation"]
    assert len(delegation_edges) == 10, f"Expected 10 delegation edges, got {len(delegation_edges)}"
    root_id = root_node["id"]
    for e in delegation_edges:
        assert e["from"] == root_id, f"Edge from {e['from']} != root {root_id}"

    # all sub-agents finished
    for nid, n in sub_agents.items():
        assert n["status"] == "finished", f"Sub-agent {n['name']} ({nid}) status={n['status']}"

    # ====================================================================
    # Phase B: call history assertions
    # ====================================================================
    history = fake_llm._call_history

    # count calls per agent
    from collections import Counter
    call_counts = Counter(h["agent_name"] for h in history)
    assert 10 <= call_counts["DeepAnalysisRoot"] <= 15, \
        f"Root calls: {call_counts['DeepAnalysisRoot']}"
    assert 2 <= call_counts["FileClassifier"] <= 5, \
        f"FileClassifier calls: {call_counts['FileClassifier']}"
    assert 1 <= call_counts["StaticScanner"] <= 4, \
        f"StaticScanner calls: {call_counts['StaticScanner']}"
    assert 2 <= call_counts["PerFileAnalyzer"] <= 4, \
        f"PerFileAnalyzer calls: {call_counts['PerFileAnalyzer']}"
    assert 1 <= call_counts["CrossLinkAnalyzer"] <= 3, \
        f"CrossLinkAnalyzer calls: {call_counts['CrossLinkAnalyzer']}"
    assert 1 <= call_counts["ReviewRefiner"] <= 3, \
        f"ReviewRefiner calls: {call_counts['ReviewRefiner']}"

    # Root orchestration pattern: create_agent -> wait_for_message alternation
    root_history = [h for h in history if h["agent_name"] == "DeepAnalysisRoot"]
    root_tool_names = []
    for h in root_history:
        # tools available in that iteration
        root_tool_names.append(h["tool_names"])

    # Verify root had access to graph tools (create_agent, wait_for_message, etc.)
    # in every iteration
    for tn in root_tool_names:
        assert "create_agent" in tn or "wait_for_message" in tn or "agent_finish" in tn, \
            f"Root iteration missing graph tools: {tn}"

    # ====================================================================
    # Phase C: IPC output assertions
    # ====================================================================
    output = fake_stdout.getvalue().strip()
    assert output, "Should produce IPC output"

    result = json.loads(output)
    assert result["type"] == "result"
    assert result["task_id"] == "t-integration-deep"
    assert result["payload"]["status"] == "ok"
    assert result["payload"]["stage"] == "deep_analysis"
    assert "findings" in result["payload"]
    assert isinstance(result["payload"]["findings"], list)
    assert result["payload"]["timings"]["total"] > 0


@pytest.mark.integration
def test_orchestration_error_on_empty_target(tmp_path, monkeypatch):
    """When the target directory is empty, the IPC response still returns valid JSON."""
    empty_dir = tmp_path / "empty_target"
    empty_dir.mkdir()

    fake_llm = ScriptedMultiAgentLLM()
    fake_llm.register_sequence("DeepAnalysisRoot", [
        _agent_finish_response("No files to analyze", report_to_parent=False),
    ])

    monkeypatch.setattr(
        "nano_strix.agents.deep_analysis.create_provider",
        lambda config: fake_llm,
    )
    monkeypatch.setattr(
        "nano_strix.agents.deep_analysis.load_config",
        lambda path: _build_test_config(),
    )
    monkeypatch.setattr(
        "nano_strix.agents.deep_analysis._docker_is_available",
        lambda: False,
    )
    monkeypatch.setattr(
        "nano_strix.agents.deep_analysis._heartbeat_loop",
        _noop_heartbeat,
    )

    task_input = json.dumps({
        "type": "task",
        "task_id": "t-empty-target",
        "stage": "deep_analysis",
        "payload": {
            "target": str(empty_dir),
            "workspace": str(tmp_path),
            "stage_results": {},
        },
    })

    fake_stdin = io.StringIO(task_input + "\n")
    fake_stdout = io.StringIO()
    monkeypatch.setattr("sys.stdin", fake_stdin)
    monkeypatch.setattr("sys.stdout", fake_stdout)

    from nano_strix.agents.deep_analysis import main

    main()

    output = fake_stdout.getvalue().strip()
    result = json.loads(output)
    assert result["type"] == "result"
    assert result["payload"]["status"] in ("ok", "error")
    assert result["payload"]["stage"] == "deep_analysis"
