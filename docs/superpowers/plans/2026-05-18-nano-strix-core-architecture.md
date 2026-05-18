# nano-strix 核心架构实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 构建 nano-strix 的核心架构，包括可配置 Pipeline、EventBus、LLM 适配层、IPC 协议、沙箱系统、日志系统和 CLI。

**Architecture:** 采用 nanobot（编排器）+ strix（专用代理）的分层架构。编排器通过 stdin/stdout JSON lines 与独立子进程的 strix 代理通信。各代理在沙箱中执行，所有操作通过结构化日志记录。LLM 层可插拔，支持任意 provider。

**Tech Stack:** Python 3.12+, Click (CLI), asyncio (并发), Docker SDK (沙箱), dataclasses (数据模型)

**Spec:** `docs/superpowers/specs/2026-05-18-nanobot-core-architecture-design.md`

---

## 文件结构

```
src/nano_strix/
├── cli.py                         # Click 命令入口（已有，需扩展）
├── shared/
│   ├── __init__.py
│   └── models.py                  # Finding, ExploitResult 等共享数据模型
├── config/
│   ├── __init__.py
│   ├── schema.py                  # LLMConfig, PipelineConfig, SandboxConfig, IPCConfig, LoggingConfig
│   ├── loader.py                  # YAML 配置加载
│   └── paths.py                   # 路径常量（workspace 根目录等）
├── llm/
│   ├── __init__.py
│   ├── adapter.py                 # LLMProvider ABC, LLMResponse, ToolCall
│   ├── registry.py                # provider 注册表
│   ├── factory.py                 # create_provider 工厂
│   ├── anthropic.py               # Claude 实现
│   ├── openai.py                  # OpenAI 实现
│   └── local.py                   # 本地模型实现
├── logging/
│   ├── __init__.py
│   ├── logger.py                  # LogEntry, 统一日志接口
│   ├── task_logger.py             # 任务状态日志
│   ├── llm_logger.py              # LLM 调用日志
│   └── tool_logger.py             # 工具调用日志
├── bus/
│   ├── __init__.py
│   ├── events.py                  # TaskEvent, TaskState, PipelineConfig
│   └── queue.py                   # EventBus（发布/订阅 + 状态持久化）
├── sandbox/
│   ├── __init__.py
│   ├── base.py                    # Sandbox ABC, SandboxConfig, ExecutionResult
│   ├── docker.py                  # DockerSandbox
│   ├── process.py                 # ProcessSandbox
│   └── manager.py                 # SandboxManager（生命周期管理）
├── agents/
│   ├── __init__.py
│   ├── base.py                    # BaseAgent（IPC 通信抽象）
│   ├── manager.py                 # AgentManager（子进程生命周期）
│   ├── per_file.py                # strix-per-file 启动器
│   ├── cross_file.py              # strix-cross-file 启动器
│   └── exploit.py                 # strix-exploit 启动器
├── orchestrator/
│   ├── __init__.py
│   ├── runner.py                  # OrchestratorRunner（主编排循环）
│   ├── planner.py                 # 分析策略规划
│   └── aggregator.py              # 结果汇总
├── report/
│   ├── __init__.py
│   ├── generator.py               # ReportGenerator
│   ├── templates/                 # 报告模板
│   │   └── report.md
│   └── attack_graph.py            # 攻击路径图生成
└── templates/
    ├── orchestrator.md            # 主 agent system prompt
    ├── per_file.md                # per-file agent prompt
    ├── cross_file.md              # cross-file agent prompt
    └── exploit.md                 # exploit agent prompt

tests/
├── conftest.py
├── test_shared_models.py
├── test_config.py
├── test_llm_adapter.py
├── test_llm_registry.py
├── test_logging.py
├── test_bus_events.py
├── test_bus_queue.py
├── test_sandbox_base.py
├── test_sandbox_process.py
├── test_agents_base.py
├── test_agents_manager.py
├── test_orchestrator_runner.py
├── test_report_generator.py
└── test_cli.py
```

---

## Phase 1: 数据模型与配置

### Task 1: 共享数据模型

**Files:**
- Create: `src/nano_strix/shared/__init__.py`
- Create: `src/nano_strix/shared/models.py`
- Create: `tests/test_shared_models.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_shared_models.py
from nano_strix.shared.models import Finding, ExploitResult


def test_finding_creation():
    f = Finding(
        id="f-001",
        title="SQL Injection in login",
        severity="critical",
        category="sql_injection",
        file_path="src/auth.py",
        line_range=(42, 58),
        description="User input directly interpolated into SQL query",
        code_snippet="query = f\"SELECT * FROM users WHERE id={user_id}\"",
        recommendation="Use parameterized queries",
        confidence=0.95,
    )
    assert f.id == "f-001"
    assert f.severity == "critical"
    assert f.confidence == 0.95


def test_finding_to_dict():
    f = Finding(
        id="f-001",
        title="XSS",
        severity="high",
        category="xss",
        file_path="src/views.py",
        line_range=(10, 20),
        description="Reflected XSS",
        code_snippet="echo(user_input)",
        recommendation="Sanitize output",
        confidence=0.8,
    )
    d = f.to_dict()
    assert d["id"] == "f-001"
    assert d["severity"] == "high"
    assert isinstance(d, dict)


def test_finding_from_dict():
    data = {
        "id": "f-001",
        "title": "XSS",
        "severity": "high",
        "category": "xss",
        "file_path": "src/views.py",
        "line_range": [10, 20],
        "description": "Reflected XSS",
        "code_snippet": "echo(user_input)",
        "recommendation": "Sanitize output",
        "confidence": 0.8,
        "metadata": {},
    }
    f = Finding.from_dict(data)
    assert f.id == "f-001"
    assert f.line_range == (10, 20)


def test_exploit_result_creation():
    r = ExploitResult(
        finding_id="f-001",
        verified=True,
        poc_script="poc_auth_sqli.py",
        output="Successfully extracted admin password",
        exit_code=0,
    )
    assert r.verified is True
    assert r.finding_id == "f-001"


def test_exploit_result_to_dict():
    r = ExploitResult(
        finding_id="f-001",
        verified=True,
        poc_script="poc.py",
        output="ok",
        exit_code=0,
    )
    d = r.to_dict()
    assert d["verified"] is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_shared_models.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'nano_strix.shared'`

- [ ] **Step 3: Write implementation**

```python
# src/nano_strix/shared/__init__.py
```

```python
# src/nano_strix/shared/models.py
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Finding:
    id: str
    title: str
    severity: str  # critical / high / medium / low / info
    category: str  # sql_injection / xss / rce / ...
    file_path: str
    line_range: tuple[int, int]
    description: str
    code_snippet: str
    recommendation: str
    confidence: float  # 0-1
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "severity": self.severity,
            "category": self.category,
            "file_path": self.file_path,
            "line_range": list(self.line_range),
            "description": self.description,
            "code_snippet": self.code_snippet,
            "recommendation": self.recommendation,
            "confidence": self.confidence,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Finding:
        lr = data["line_range"]
        return cls(
            id=data["id"],
            title=data["title"],
            severity=data["severity"],
            category=data["category"],
            file_path=data["file_path"],
            line_range=(lr[0], lr[1]),
            description=data["description"],
            code_snippet=data["code_snippet"],
            recommendation=data["recommendation"],
            confidence=data["confidence"],
            metadata=data.get("metadata", {}),
        )


@dataclass
class ExploitResult:
    finding_id: str
    verified: bool
    poc_script: str
    output: str
    exit_code: int
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "finding_id": self.finding_id,
            "verified": self.verified,
            "poc_script": self.poc_script,
            "output": self.output,
            "exit_code": self.exit_code,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ExploitResult:
        return cls(
            finding_id=data["finding_id"],
            verified=data["verified"],
            poc_script=data["poc_script"],
            output=data["output"],
            exit_code=data["exit_code"],
            metadata=data.get("metadata", {}),
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_shared_models.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add src/nano_strix/shared/ tests/test_shared_models.py
git commit -m "feat: add shared data models (Finding, ExploitResult)"
```

---

### Task 2: 配置 schema 与加载

**Files:**
- Create: `src/nano_strix/config/__init__.py`
- Create: `src/nano_strix/config/schema.py`
- Create: `src/nano_strix/config/loader.py`
- Create: `src/nano_strix/config/paths.py`
- Create: `tests/test_config.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_config.py
import tempfile
from pathlib import Path

from nano_strix.config.schema import (
    AppConfig,
    LLMConfig,
    PipelineConfig,
    SandboxConfig,
    IPCConfig,
    LoggingConfig,
)
from nano_strix.config.loader import load_config
from nano_strix.config.paths import DEFAULT_WORKSPACE_ROOT


def test_default_config():
    cfg = AppConfig()
    assert cfg.llm.provider == "anthropic"
    assert cfg.pipeline.stages == ["per_file", "cross_file", "exploit", "report"]
    assert cfg.ipc.timeout_seconds == 300


def test_sandbox_config():
    cfg = SandboxConfig(sandbox_type="docker", image="python:3.12-slim")
    assert cfg.sandbox_type == "docker"
    assert cfg.network == "none"
    assert cfg.memory_limit == "512m"


def test_pipeline_presets():
    cfg = PipelineConfig()
    assert cfg.stages == ["per_file", "cross_file", "exploit", "report"]


def test_load_config_from_yaml():
    yaml_content = """
llm:
  provider: openai
  api_key: sk-test
  model: gpt-4o
pipeline:
  stages:
    - per_file
    - report
ipc:
  timeout_seconds: 600
"""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write(yaml_content)
        f.flush()
        cfg = load_config(Path(f.name))

    assert cfg.llm.provider == "openai"
    assert cfg.llm.api_key == "sk-test"
    assert cfg.pipeline.stages == ["per_file", "report"]
    assert cfg.ipc.timeout_seconds == 600


def test_load_config_missing_file():
    cfg = load_config(Path("/nonexistent/config.yaml"))
    assert cfg.llm.provider == "anthropic"  # falls back to defaults


def test_logging_config():
    cfg = LoggingConfig(level="debug")
    assert cfg.level == "debug"
    assert cfg.categories == {}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_config.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Write implementation**

```python
# src/nano_strix/config/__init__.py
```

```python
# src/nano_strix/config/schema.py
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class LLMConfig:
    provider: str = "anthropic"
    api_key: str = ""
    base_url: str = ""
    model: str = ""
    models: dict[str, str] = field(default_factory=dict)


@dataclass
class PipelineConfig:
    stages: list[str] = field(
        default_factory=lambda: ["per_file", "cross_file", "exploit", "report"]
    )
    input_overrides: dict[str, str] = field(default_factory=dict)


@dataclass
class SandboxConfig:
    sandbox_type: str = "docker"  # docker / process
    image: str = "python:3.12-slim"
    network: str = "none"
    memory_limit: str = "512m"
    cpu_limit: float = 1.0
    timeout: int = 600
    env_vars: dict[str, str] = field(default_factory=dict)
    volumes: list[dict] = field(default_factory=list)


@dataclass
class IPCConfig:
    timeout_seconds: int = 300
    max_retries: int = 2
    retry_delay_seconds: int = 5


@dataclass
class LoggingConfig:
    level: str = "info"
    categories: dict[str, str] = field(default_factory=dict)


@dataclass
class AppConfig:
    llm: LLMConfig = field(default_factory=LLMConfig)
    pipeline: PipelineConfig = field(default_factory=PipelineConfig)
    sandbox: SandboxConfig = field(default_factory=SandboxConfig)
    ipc: IPCConfig = field(default_factory=IPCConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)
```

```python
# src/nano_strix/config/loader.py
from __future__ import annotations

from pathlib import Path

import yaml

from nano_strix.config.schema import (
    AppConfig,
    IPCConfig,
    LLMConfig,
    LoggingConfig,
    PipelineConfig,
    SandboxConfig,
)


def load_config(path: Path) -> AppConfig:
    if not path.exists():
        return AppConfig()

    with open(path) as f:
        data = yaml.safe_load(f) or {}

    llm_data = data.get("llm", {})
    pipeline_data = data.get("pipeline", {})
    sandbox_data = data.get("sandbox", {})
    ipc_data = data.get("ipc", {})
    logging_data = data.get("logging", {})

    return AppConfig(
        llm=LLMConfig(**llm_data) if llm_data else LLMConfig(),
        pipeline=PipelineConfig(**pipeline_data) if pipeline_data else PipelineConfig(),
        sandbox=SandboxConfig(**sandbox_data) if sandbox_data else SandboxConfig(),
        ipc=IPCConfig(**ipc_data) if ipc_data else IPCConfig(),
        logging=LoggingConfig(**logging_data) if logging_data else LoggingConfig(),
    )
```

```python
# src/nano_strix/config/paths.py
from pathlib import Path

DEFAULT_WORKSPACE_ROOT = Path.home() / ".nano-strix" / "workspace"
DEFAULT_CONFIG_PATH = Path.home() / ".nano-strix" / "config.yaml"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_config.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add src/nano_strix/config/ tests/test_config.py
git commit -m "feat: add config schema and YAML loader"
```

---

## Phase 2: LLM 适配层

### Task 3: LLM Provider 抽象与注册

**Files:**
- Create: `src/nano_strix/llm/__init__.py`
- Create: `src/nano_strix/llm/adapter.py`
- Create: `src/nano_strix/llm/registry.py`
- Create: `src/nano_strix/llm/factory.py`
- Create: `tests/test_llm_adapter.py`
- Create: `tests/test_llm_registry.py`

- [ ] **Step 1: Write failing tests for adapter**

```python
# tests/test_llm_adapter.py
import pytest
from nano_strix.llm.adapter import LLMProvider, LLMResponse, ToolCall


def test_tool_call_creation():
    tc = ToolCall(id="tc-1", name="read_file", arguments={"path": "src/auth.py"})
    assert tc.id == "tc-1"
    assert tc.name == "read_file"


def test_llm_response_properties():
    resp = LLMResponse(
        content=None,
        tool_calls=[ToolCall(id="tc-1", name="read_file", arguments={})],
        finish_reason="tool_calls",
        usage={"input_tokens": 100, "output_tokens": 50},
        model="claude-sonnet-4-6",
    )
    assert resp.has_tool_calls is True
    assert resp.should_execute_tools is True


def test_llm_response_no_tools():
    resp = LLMResponse(
        content="Here is my analysis...",
        tool_calls=[],
        finish_reason="stop",
        usage={"input_tokens": 100, "output_tokens": 200},
        model="claude-sonnet-4-6",
    )
    assert resp.has_tool_calls is False
    assert resp.should_execute_tools is False


def test_llm_provider_is_abstract():
    with pytest.raises(TypeError):
        LLMProvider()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_llm_adapter.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Write adapter implementation**

```python
# src/nano_strix/llm/__init__.py
```

```python
# src/nano_strix/llm/adapter.py
from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class LLMResponse:
    content: str | None
    tool_calls: list[ToolCall] = field(default_factory=list)
    finish_reason: str = "stop"
    usage: dict[str, int] = field(default_factory=dict)
    model: str = ""

    @property
    def has_tool_calls(self) -> bool:
        return len(self.tool_calls) > 0

    @property
    def should_execute_tools(self) -> bool:
        if not self.has_tool_calls:
            return False
        return self.finish_reason in ("tool_calls", "stop")


class LLMProvider(ABC):
    @abstractmethod
    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        ...

    @abstractmethod
    async def stream_chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> AsyncIterator[str]:
        ...
```

- [ ] **Step 4: Run adapter tests**

Run: `.venv/bin/pytest tests/test_llm_adapter.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Write failing tests for registry and factory**

```python
# tests/test_llm_registry.py
import pytest
from nano_strix.llm.registry import register_provider, get_provider_class, PROVIDER_REGISTRY
from nano_strix.llm.factory import create_provider
from nano_strix.llm.adapter import LLMProvider, LLMResponse
from nano_strix.config.schema import LLMConfig
from collections.abc import AsyncIterator
from typing import Any


class FakeProvider(LLMProvider):
    async def chat(self, messages, tools=None, temperature=0.7, max_tokens=4096):
        return LLMResponse(content="fake", model="fake-model")

    async def stream_chat(self, messages, tools=None, temperature=0.7, max_tokens=4096):
        yield "fake"


def test_register_provider():
    # Clean up after test
    original = dict(PROVIDER_REGISTRY)
    try:
        register_provider("fake")(FakeProvider)
        assert "fake" in PROVIDER_REGISTRY
        assert PROVIDER_REGISTRY["fake"] is FakeProvider
    finally:
        PROVIDER_REGISTRY.clear()
        PROVIDER_REGISTRY.update(original)


def test_get_provider_class():
    original = dict(PROVIDER_REGISTRY)
    try:
        register_provider("fake")(FakeProvider)
        cls = get_provider_class("fake")
        assert cls is FakeProvider
    finally:
        PROVIDER_REGISTRY.clear()
        PROVIDER_REGISTRY.update(original)


def test_get_unknown_provider():
    with pytest.raises(KeyError):
        get_provider_class("nonexistent")


def test_create_provider():
    original = dict(PROVIDER_REGISTRY)
    try:
        register_provider("fake")(FakeProvider)
        config = LLMConfig(provider="fake", api_key="test-key", model="fake-model")
        provider = create_provider(config)
        assert isinstance(provider, FakeProvider)
    finally:
        PROVIDER_REGISTRY.clear()
        PROVIDER_REGISTRY.update(original)
```

- [ ] **Step 6: Run registry/factory tests to verify they fail**

Run: `.venv/bin/pytest tests/test_llm_registry.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 7: Write registry and factory implementation**

```python
# src/nano_strix/llm/registry.py
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from nano_strix.llm.adapter import LLMProvider

PROVIDER_REGISTRY: dict[str, type[LLMProvider]] = {}


def register_provider(name: str):
    def decorator(cls: type[LLMProvider]) -> type[LLMProvider]:
        PROVIDER_REGISTRY[name] = cls
        return cls
    return decorator


def get_provider_class(name: str) -> type[LLMProvider]:
    if name not in PROVIDER_REGISTRY:
        raise KeyError(f"Unknown provider: {name}. Registered: {list(PROVIDER_REGISTRY.keys())}")
    return PROVIDER_REGISTRY[name]
```

```python
# src/nano_strix/llm/factory.py
from __future__ import annotations

from nano_strix.config.schema import LLMConfig
from nano_strix.llm.adapter import LLMProvider
from nano_strix.llm.registry import get_provider_class


def create_provider(config: LLMConfig) -> LLMProvider:
    cls = get_provider_class(config.provider)
    return cls(api_key=config.api_key, base_url=config.base_url, model=config.model)
```

- [ ] **Step 8: Run all LLM tests**

Run: `.venv/bin/pytest tests/test_llm_adapter.py tests/test_llm_registry.py -v`
Expected: PASS (8 tests)

- [ ] **Step 9: Commit**

```bash
git add src/nano_strix/llm/ tests/test_llm_adapter.py tests/test_llm_registry.py
git commit -m "feat: add LLM provider abstraction, registry, and factory"
```

---

### Task 4: Anthropic Provider 实现

**Files:**
- Create: `src/nano_strix/llm/anthropic.py`
- Modify: `src/nano_strix/llm/__init__.py` (import for auto-registration)

- [ ] **Step 1: Write failing tests**

```python
# tests/test_llm_anthropic.py
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from nano_strix.llm.anthropic import AnthropicProvider
from nano_strix.llm.adapter import LLMResponse


@pytest.fixture
def provider():
    return AnthropicProvider(api_key="test-key", model="claude-sonnet-4-6")


def test_anthropic_provider_creation(provider):
    assert provider.model == "claude-sonnet-4-6"


@pytest.mark.asyncio
async def test_anthropic_chat(provider):
    mock_response = MagicMock()
    mock_response.content = [MagicMock(text="Hello")]
    mock_response.stop_reason = "end_turn"
    mock_response.usage = MagicMock(input_tokens=10, output_tokens=5)

    with patch.object(provider._client.messages, "create", new_callable=AsyncMock, return_value=mock_response):
        resp = await provider.chat([{"role": "user", "content": "Hi"}])
        assert isinstance(resp, LLMResponse)
        assert resp.content == "Hello"
        assert resp.finish_reason == "stop"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_llm_anthropic.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Write Anthropic provider**

```python
# src/nano_strix/llm/anthropic.py
from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from nano_strix.llm.adapter import LLMProvider, LLMResponse, ToolCall
from nano_strix.llm.registry import register_provider


@register_provider("anthropic")
class AnthropicProvider(LLMProvider):
    def __init__(self, api_key: str = "", base_url: str = "", model: str = "claude-sonnet-4-6") -> None:
        import anthropic

        self.model = model
        self._client = anthropic.AsyncAnthropic(
            api_key=api_key or None,
            base_url=base_url or None,
        )

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if tools:
            kwargs["tools"] = tools

        response = await self._client.messages.create(**kwargs)

        content = None
        tool_calls = []
        for block in response.content:
            if block.type == "text":
                content = block.text
            elif block.type == "tool_use":
                tool_calls.append(ToolCall(id=block.id, name=block.name, arguments=block.input))

        finish_reason = "stop" if response.stop_reason == "end_turn" else response.stop_reason

        return LLMResponse(
            content=content,
            tool_calls=tool_calls,
            finish_reason=finish_reason,
            usage={"input_tokens": response.usage.input_tokens, "output_tokens": response.usage.output_tokens},
            model=self.model,
        )

    async def stream_chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> AsyncIterator[str]:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if tools:
            kwargs["tools"] = tools

        async with self._client.messages.stream(**kwargs) as stream:
            async for text in stream.text_stream:
                yield text
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/pytest tests/test_llm_anthropic.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add src/nano_strix/llm/anthropic.py tests/test_llm_anthropic.py
git commit -m "feat: add Anthropic LLM provider implementation"
```

---

## Phase 3: 日志系统

### Task 5: 结构化日志

**Files:**
- Create: `src/nano_strix/logging/__init__.py`
- Create: `src/nano_strix/logging/logger.py`
- Create: `src/nano_strix/logging/task_logger.py`
- Create: `src/nano_strix/logging/llm_logger.py`
- Create: `src/nano_strix/logging/tool_logger.py`
- Create: `tests/test_logging.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_logging.py
import json
from datetime import datetime
from pathlib import Path

from nano_strix.logging.logger import LogEntry, JSONLLogger
from nano_strix.logging.task_logger import TaskLogger
from nano_strix.logging.llm_logger import LLMLogger
from nano_strix.logging.tool_logger import ToolLogger


def test_log_entry_creation():
    entry = LogEntry(
        task_id="t-001",
        stage="per_file",
        category="task",
        level="info",
        event="task_started",
        data={"target": "src/auth.py"},
    )
    assert entry.task_id == "t-001"
    assert entry.category == "task"


def test_log_entry_to_json():
    entry = LogEntry(
        task_id="t-001",
        stage="per_file",
        category="llm",
        level="info",
        event="chat_request",
        data={"model": "claude-sonnet-4-6", "input_tokens": 100},
        duration=1.2,
    )
    j = entry.to_json()
    data = json.loads(j)
    assert data["task_id"] == "t-001"
    assert data["category"] == "llm"
    assert data["data"]["input_tokens"] == 100


def test_jsonl_logger_writes(tmp_path: Path):
    log_file = tmp_path / "test.jsonl"
    logger = JSONLLogger(log_file)
    entry = LogEntry(
        task_id="t-001",
        stage=None,
        category="task",
        level="info",
        event="created",
        data={},
    )
    logger.write(entry)
    lines = log_file.read_text().strip().split("\n")
    assert len(lines) == 1
    data = json.loads(lines[0])
    assert data["event"] == "created"


def test_task_logger(tmp_path: Path):
    log_file = tmp_path / "task.jsonl"
    logger = TaskLogger(log_file)
    logger.task_started("t-001", "per_file", {"target": "src/"})
    logger.task_completed("t-001", "per_file", {"findings_count": 3})

    lines = log_file.read_text().strip().split("\n")
    assert len(lines) == 2
    assert json.loads(lines[0])["event"] == "stage_started"
    assert json.loads(lines[1])["event"] == "stage_completed"


def test_llm_logger(tmp_path: Path):
    log_file = tmp_path / "llm.jsonl"
    logger = LLMLogger(log_file)
    logger.log_request("t-001", "per_file", "claude-sonnet-4-6", 5, 3)
    logger.log_response("t-001", "per_file", 2048, 512, 1200.0, "tool_calls")

    lines = log_file.read_text().strip().split("\n")
    assert len(lines) == 2
    assert json.loads(lines[0])["event"] == "llm_request"
    assert json.loads(lines[1])["event"] == "llm_response"


def test_tool_logger(tmp_path: Path):
    log_file = tmp_path / "tool.jsonl"
    logger = ToolLogger(log_file)
    logger.log_execution("t-001", "per_file", "read_file", {"path": "src/a.py"}, 1523, 5.0)

    lines = log_file.read_text().strip().split("\n")
    assert len(lines) == 1
    data = json.loads(lines[0])
    assert data["event"] == "tool_execution"
    assert data["data"]["tool"] == "read_file"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_logging.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Write implementation**

```python
# src/nano_strix/logging/__init__.py
```

```python
# src/nano_strix/logging/logger.py
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass
class LogEntry:
    task_id: str
    stage: str | None
    category: str  # task / llm / tool / sandbox / ipc
    level: str     # debug / info / warning / error
    event: str
    data: dict[str, Any]
    duration: float | None = None
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_json(self) -> str:
        return json.dumps({
            "timestamp": self.timestamp.isoformat(),
            "task_id": self.task_id,
            "stage": self.stage,
            "category": self.category,
            "level": self.level,
            "event": self.event,
            "data": self.data,
            "duration": self.duration,
        }, ensure_ascii=False)


class JSONLLogger:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, entry: LogEntry) -> None:
        with open(self._path, "a") as f:
            f.write(entry.to_json() + "\n")
```

```python
# src/nano_strix/logging/task_logger.py
from __future__ import annotations

from pathlib import Path
from typing import Any

from nano_strix.logging.logger import JSONLLogger, LogEntry


class TaskLogger:
    def __init__(self, path: Path) -> None:
        self._logger = JSONLLogger(path)

    def task_created(self, task_id: str, stages: list[str]) -> None:
        self._logger.write(LogEntry(
            task_id=task_id, stage=None, category="task", level="info",
            event="task_created", data={"stages": stages},
        ))

    def task_started(self, task_id: str, stage: str, data: dict[str, Any] | None = None) -> None:
        self._logger.write(LogEntry(
            task_id=task_id, stage=stage, category="task", level="info",
            event="stage_started", data=data or {},
        ))

    def task_completed(self, task_id: str, stage: str, data: dict[str, Any] | None = None) -> None:
        self._logger.write(LogEntry(
            task_id=task_id, stage=stage, category="task", level="info",
            event="stage_completed", data=data or {},
        ))

    def task_failed(self, task_id: str, stage: str, error: str) -> None:
        self._logger.write(LogEntry(
            task_id=task_id, stage=stage, category="task", level="error",
            event="stage_failed", data={"error": error},
        ))
```

```python
# src/nano_strix/logging/llm_logger.py
from __future__ import annotations

from pathlib import Path

from nano_strix.logging.logger import JSONLLogger, LogEntry


class LLMLogger:
    def __init__(self, path: Path) -> None:
        self._logger = JSONLLogger(path)

    def log_request(self, task_id: str, stage: str, model: str, messages_count: int, tools_count: int) -> None:
        self._logger.write(LogEntry(
            task_id=task_id, stage=stage, category="llm", level="debug",
            event="llm_request", data={
                "model": model, "messages_count": messages_count, "tools_count": tools_count,
            },
        ))

    def log_response(self, task_id: str, stage: str, input_tokens: int, output_tokens: int, latency_ms: float, finish_reason: str) -> None:
        self._logger.write(LogEntry(
            task_id=task_id, stage=stage, category="llm", level="info",
            event="llm_response", data={
                "input_tokens": input_tokens, "output_tokens": output_tokens,
                "latency_ms": latency_ms, "finish_reason": finish_reason,
            },
            duration=latency_ms / 1000,
        ))
```

```python
# src/nano_strix/logging/tool_logger.py
from __future__ import annotations

from pathlib import Path
from typing import Any

from nano_strix.logging.logger import JSONLLogger, LogEntry


class ToolLogger:
    def __init__(self, path: Path) -> None:
        self._logger = JSONLLogger(path)

    def log_execution(self, task_id: str, stage: str, tool: str, arguments: dict[str, Any], result_chars: int, duration_ms: float) -> None:
        self._logger.write(LogEntry(
            task_id=task_id, stage=stage, category="tool", level="info",
            event="tool_execution", data={
                "tool": tool, "arguments": arguments, "result_chars": result_chars,
            },
            duration=duration_ms / 1000,
        ))
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/pytest tests/test_logging.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add src/nano_strix/logging/ tests/test_logging.py
git commit -m "feat: add structured logging system (task, llm, tool)"
```

---

## Phase 4: EventBus

### Task 6: 事件定义与 EventBus

**Files:**
- Create: `src/nano_strix/bus/__init__.py`
- Create: `src/nano_strix/bus/events.py`
- Create: `src/nano_strix/bus/queue.py`
- Create: `tests/test_bus_events.py`
- Create: `tests/test_bus_queue.py`

- [ ] **Step 1: Write failing tests for events**

```python
# tests/test_bus_events.py
from nano_strix.bus.events import TaskEvent, TaskState


def test_task_event_creation():
    event = TaskEvent(
        task_id="t-001",
        event_type="task_created",
        stage=None,
        payload={"stages": ["per_file", "report"]},
    )
    assert event.task_id == "t-001"
    assert event.event_type == "task_created"


def test_task_event_to_dict():
    event = TaskEvent(
        task_id="t-001",
        event_type="stage_started",
        stage="per_file",
        payload={},
    )
    d = event.to_dict()
    assert d["task_id"] == "t-001"
    assert d["stage"] == "per_file"


def test_task_state_creation():
    state = TaskState(
        task_id="t-001",
        stages=["per_file", "cross_file", "report"],
        current_stage=None,
        status="pending",
    )
    assert state.status == "pending"
    assert state.stage_results == {}


def test_task_state_advance():
    state = TaskState(
        task_id="t-001",
        stages=["per_file", "cross_file", "report"],
        current_stage=None,
        status="pending",
    )
    state.advance("per_file")
    assert state.current_stage == "per_file"
    assert state.status == "running"


def test_task_state_complete_stage():
    state = TaskState(
        task_id="t-001",
        stages=["per_file", "cross_file", "report"],
        current_stage="per_file",
        status="running",
    )
    state.complete_stage("per_file", {"output": "results/per_file_findings.json"})
    assert state.stage_results["per_file"] == {"output": "results/per_file_findings.json"}
    assert state.current_stage is None


def test_task_state_is_complete():
    state = TaskState(
        task_id="t-001",
        stages=["per_file"],
        current_stage=None,
        status="running",
        stage_results={"per_file": {}},
    )
    assert state.is_complete is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_bus_events.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Write events implementation**

```python
# src/nano_strix/bus/__init__.py
```

```python
# src/nano_strix/bus/events.py
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass
class TaskEvent:
    task_id: str
    event_type: str  # task_created / task_started / stage_started / stage_completed / stage_failed / task_completed / task_failed
    stage: str | None
    payload: dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "event_type": self.event_type,
            "stage": self.stage,
            "payload": self.payload,
            "timestamp": self.timestamp.isoformat(),
        }


@dataclass
class TaskState:
    task_id: str
    stages: list[str]
    current_stage: str | None
    status: str  # pending / running / completed / failed
    stage_results: dict[str, Any] = field(default_factory=dict)
    error: str | None = None

    @property
    def is_complete(self) -> bool:
        return all(s in self.stage_results for s in self.stages)

    def advance(self, stage: str) -> None:
        self.current_stage = stage
        self.status = "running"

    def complete_stage(self, stage: str, result: Any) -> None:
        self.stage_results[stage] = result
        self.current_stage = None

    def fail(self, error: str) -> None:
        self.status = "failed"
        self.error = error
```

- [ ] **Step 4: Run event tests**

Run: `.venv/bin/pytest tests/test_bus_events.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Write failing tests for EventBus**

```python
# tests/test_bus_queue.py
from pathlib import Path

from nano_strix.bus.events import TaskEvent, TaskState
from nano_strix.bus.queue import EventBus


def test_event_bus_create_task(tmp_path: Path):
    bus = EventBus(tmp_path)
    state = bus.create_task(["per_file", "report"])
    assert state.status == "pending"
    assert state.stages == ["per_file", "report"]
    assert (tmp_path / f"{state.task_id}" / "state.json").exists()


def test_event_bus_publish_and_get_events(tmp_path: Path):
    bus = EventBus(tmp_path)
    state = bus.create_task(["per_file"])

    event = TaskEvent(task_id=state.task_id, event_type="task_started", stage="per_file")
    bus.publish(event)

    events = bus.get_events(state.task_id)
    assert len(events) == 1
    assert events[0].event_type == "task_started"


def test_event_bus_update_state(tmp_path: Path):
    bus = EventBus(tmp_path)
    state = bus.create_task(["per_file"])
    state.advance("per_file")
    bus.update_state(state)

    loaded = bus.get_state(state.task_id)
    assert loaded.current_stage == "per_file"
    assert loaded.status == "running"


def test_event_bus_get_pending_tasks(tmp_path: Path):
    bus = EventBus(tmp_path)
    bus.create_task(["per_file"])
    bus.create_task(["exploit"])

    pending = bus.get_pending_tasks()
    assert len(pending) == 2
```

- [ ] **Step 6: Run EventBus tests to verify they fail**

Run: `.venv/bin/pytest tests/test_bus_queue.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 7: Write EventBus implementation**

```python
# src/nano_strix/bus/queue.py
from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

from nano_strix.bus.events import TaskEvent, TaskState


class EventBus:
    def __init__(self, workspace_root: Path) -> None:
        self._root = workspace_root
        self._root.mkdir(parents=True, exist_ok=True)

    def create_task(self, stages: list[str]) -> TaskState:
        task_id = f"t-{uuid.uuid4().hex[:8]}"
        task_dir = self._root / task_id
        task_dir.mkdir(parents=True)

        state = TaskState(task_id=task_id, stages=stages, current_stage=None, status="pending")
        self.update_state(state)
        return state

    def publish(self, event: TaskEvent) -> None:
        events_file = self._root / event.task_id / "events.jsonl"
        with open(events_file, "a") as f:
            f.write(json.dumps(event.to_dict(), ensure_ascii=False) + "\n")

    def get_events(self, task_id: str) -> list[TaskEvent]:
        events_file = self._root / task_id / "events.jsonl"
        if not events_file.exists():
            return []
        events = []
        for line in events_file.read_text().strip().split("\n"):
            if not line:
                continue
            data = json.loads(line)
            events.append(TaskEvent(
                task_id=data["task_id"],
                event_type=data["event_type"],
                stage=data.get("stage"),
                payload=data.get("payload", {}),
            ))
        return events

    def update_state(self, state: TaskState) -> None:
        state_file = self._root / state.task_id / "state.json"
        state_file.parent.mkdir(parents=True, exist_ok=True)
        with open(state_file, "w") as f:
            json.dump({
                "task_id": state.task_id,
                "stages": state.stages,
                "current_stage": state.current_stage,
                "status": state.status,
                "stage_results": state.stage_results,
                "error": state.error,
            }, f, ensure_ascii=False, indent=2)

    def get_state(self, task_id: str) -> TaskState:
        state_file = self._root / task_id / "state.json"
        with open(state_file) as f:
            data = json.load(f)
        return TaskState(
            task_id=data["task_id"],
            stages=data["stages"],
            current_stage=data["current_stage"],
            status=data["status"],
            stage_results=data.get("stage_results", {}),
            error=data.get("error"),
        )

    def get_pending_tasks(self) -> list[TaskState]:
        tasks = []
        for task_dir in self._root.iterdir():
            if task_dir.is_dir() and (task_dir / "state.json").exists():
                state = self.get_state(task_dir.name)
                if state.status == "pending":
                    tasks.append(state)
        return tasks
```

- [ ] **Step 8: Run all bus tests**

Run: `.venv/bin/pytest tests/test_bus_events.py tests/test_bus_queue.py -v`
Expected: PASS (10 tests)

- [ ] **Step 9: Commit**

```bash
git add src/nano_strix/bus/ tests/test_bus_events.py tests/test_bus_queue.py
git commit -m "feat: add EventBus with task state management"
```

---

## Phase 5: 沙箱系统

### Task 7: 沙箱抽象与进程沙箱

**Files:**
- Create: `src/nano_strix/sandbox/__init__.py`
- Create: `src/nano_strix/sandbox/base.py`
- Create: `src/nano_strix/sandbox/process.py`
- Create: `src/nano_strix/sandbox/docker.py`
- Create: `src/nano_strix/sandbox/manager.py`
- Create: `tests/test_sandbox_base.py`
- Create: `tests/test_sandbox_process.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_sandbox_base.py
import pytest
from nano_strix.sandbox.base import Sandbox, SandboxConfig, ExecutionResult


def test_sandbox_config_defaults():
    cfg = SandboxConfig()
    assert cfg.sandbox_type == "process"
    assert cfg.timeout == 600


def test_sandbox_is_abstract():
    with pytest.raises(TypeError):
        Sandbox()


def test_execution_result():
    r = ExecutionResult(exit_code=0, stdout="ok", stderr="", duration=0.5)
    assert r.success is True


def test_execution_result_failure():
    r = ExecutionResult(exit_code=1, stdout="", stderr="error", duration=1.0)
    assert r.success is False
```

```python
# tests/test_sandbox_process.py
import asyncio
import pytest
from pathlib import Path
from nano_strix.sandbox.process import ProcessSandbox
from nano_strix.sandbox.base import SandboxConfig


@pytest.fixture
def sandbox(tmp_path: Path):
    cfg = SandboxConfig(timeout=30)
    return ProcessSandbox(cfg, workspace=tmp_path)


@pytest.mark.asyncio
async def test_process_execute_echo(sandbox):
    result = await sandbox.execute("echo hello")
    assert result.exit_code == 0
    assert "hello" in result.stdout


@pytest.mark.asyncio
async def test_process_execute_failing_command(sandbox):
    result = await sandbox.execute("false")
    assert result.exit_code == 1


@pytest.mark.asyncio
async def test_process_copy_in_out(sandbox, tmp_path: Path):
    src = tmp_path / "input.txt"
    src.write_text("test content")

    dest = sandbox.workspace / "copied.txt"
    await sandbox.copy_in(str(src), str(dest))
    assert dest.exists()
    assert dest.read_text() == "test content"

    out = tmp_path / "output.txt"
    await sandbox.copy_out(str(dest), str(out))
    assert out.read_text() == "test content"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_sandbox_base.py tests/test_sandbox_process.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Write implementation**

```python
# src/nano_strix/sandbox/__init__.py
```

```python
# src/nano_strix/sandbox/base.py
from __future__ import annotations

import shutil
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class SandboxConfig:
    sandbox_type: str = "process"  # docker / process
    image: str = "python:3.12-slim"
    network: str = "none"
    memory_limit: str = "512m"
    cpu_limit: float = 1.0
    timeout: int = 600
    env_vars: dict[str, str] = field(default_factory=dict)
    volumes: list[dict[str, str]] = field(default_factory=list)


@dataclass
class ExecutionResult:
    exit_code: int
    stdout: str
    stderr: str
    duration: float

    @property
    def success(self) -> bool:
        return self.exit_code == 0


class Sandbox(ABC):
    @abstractmethod
    async def execute(self, command: str, timeout: int | None = None) -> ExecutionResult:
        ...

    @abstractmethod
    async def copy_in(self, local_path: str, sandbox_path: str) -> None:
        ...

    @abstractmethod
    async def copy_out(self, sandbox_path: str, local_path: str) -> None:
        ...

    @abstractmethod
    async def destroy(self) -> None:
        ...
```

```python
# src/nano_strix/sandbox/process.py
from __future__ import annotations

import asyncio
import shutil
import time
from pathlib import Path

from nano_strix.sandbox.base import ExecutionResult, Sandbox, SandboxConfig


class ProcessSandbox(Sandbox):
    def __init__(self, config: SandboxConfig, workspace: Path) -> None:
        self._config = config
        self.workspace = workspace
        self.workspace.mkdir(parents=True, exist_ok=True)

    async def execute(self, command: str, timeout: int | None = None) -> ExecutionResult:
        effective_timeout = timeout or self._config.timeout
        start = time.monotonic()

        process = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(self.workspace),
            env={**self._config.env_vars} if self._config.env_vars else None,
        )

        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(), timeout=effective_timeout
            )
            duration = time.monotonic() - start
            return ExecutionResult(
                exit_code=process.returncode or 0,
                stdout=stdout.decode(errors="replace"),
                stderr=stderr.decode(errors="replace"),
                duration=duration,
            )
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
            duration = time.monotonic() - start
            return ExecutionResult(
                exit_code=-1,
                stdout="",
                stderr=f"Command timed out after {effective_timeout}s",
                duration=duration,
            )

    async def copy_in(self, local_path: str, sandbox_path: str) -> None:
        dest = Path(sandbox_path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(local_path, dest)

    async def copy_out(self, sandbox_path: str, local_path: str) -> None:
        dest = Path(local_path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(sandbox_path, dest)

    async def destroy(self) -> None:
        pass  # Nothing to clean up for process sandbox
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/pytest tests/test_sandbox_base.py tests/test_sandbox_process.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Commit**

```bash
git add src/nano_strix/sandbox/ tests/test_sandbox_base.py tests/test_sandbox_process.py
git commit -m "feat: add sandbox abstraction and process sandbox"
```

---

## Phase 6: IPC 协议与 Agent 基础

### Task 8: IPC 协议与 BaseAgent

**Files:**
- Create: `src/nano_strix/agents/__init__.py`
- Create: `src/nano_strix/agents/base.py`
- Create: `tests/test_agents_base.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_agents_base.py
import json
import asyncio
import pytest
from nano_strix.agents.base import BaseAgent, IPCMessage


def test_ipc_message_creation():
    msg = IPCMessage(type="task", task_id="t-001", payload={"files": ["a.py"]})
    assert msg.type == "task"
    assert msg.task_id == "t-001"


def test_ipc_message_to_json():
    msg = IPCMessage(type="result", task_id="t-001", payload={"findings": []})
    j = msg.to_json()
    data = json.loads(j)
    assert data["type"] == "result"
    assert data["task_id"] == "t-001"


def test_ipc_message_from_json():
    line = json.dumps({"type": "progress", "task_id": "t-001", "detail": "analyzing..."})
    msg = IPCMessage.from_json(line)
    assert msg.type == "progress"
    assert msg.detail == "analyzing..."


def test_base_agent_is_abstract():
    with pytest.raises(TypeError):
        BaseAgent()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_agents_base.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Write implementation**

```python
# src/nano_strix/agents/__init__.py
```

```python
# src/nano_strix/agents/base.py
from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class IPCMessage:
    type: str  # task / progress / result / error / cancel
    task_id: str
    payload: dict[str, Any] = field(default_factory=dict)
    detail: str | None = None
    stage: str | None = None

    def to_json(self) -> str:
        data: dict[str, Any] = {"type": self.type, "task_id": self.task_id}
        if self.payload:
            data["payload"] = self.payload
        if self.detail:
            data["detail"] = self.detail
        if self.stage:
            data["stage"] = self.stage
        return json.dumps(data, ensure_ascii=False)

    @classmethod
    def from_json(cls, line: str) -> IPCMessage:
        data = json.loads(line)
        return cls(
            type=data["type"],
            task_id=data["task_id"],
            payload=data.get("payload", {}),
            detail=data.get("detail"),
            stage=data.get("stage"),
        )


class BaseAgent(ABC):
    @abstractmethod
    async def run(self, task_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Execute the agent's task and return results."""
        ...
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/pytest tests/test_agents_base.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add src/nano_strix/agents/ tests/test_agents_base.py
git commit -m "feat: add IPC protocol and BaseAgent abstraction"
```

---

### Task 9: Agent Manager

**Files:**
- Create: `src/nano_strix/agents/manager.py`
- Create: `tests/test_agents_manager.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_agents_manager.py
import asyncio
import json
import pytest
from pathlib import Path
from nano_strix.agents.manager import AgentManager
from nano_strix.agents.base import IPCMessage
from nano_strix.config.schema import IPCConfig


@pytest.fixture
def manager(tmp_path: Path):
    return AgentManager(workspace=tmp_path, config=IPCConfig(timeout_seconds=5))


@pytest.mark.asyncio
async def test_manager_dispatch_and_receive(manager, tmp_path: Path):
    """Test sending a task to a mock agent script and receiving result."""
    # Create a mock agent script that reads stdin and writes result to stdout
    script = tmp_path / "mock_agent.py"
    script.write_text("""
import sys, json
line = sys.stdin.readline()
msg = json.loads(line)
result = {"type": "result", "task_id": msg["task_id"], "payload": {"findings": ["f-001"]}}
print(json.dumps(result))
""")

    result = await manager.dispatch(
        agent_script=str(script),
        task_id="t-001",
        stage="per_file",
        payload={"files": ["a.py"]},
    )
    assert result["findings"] == ["f-001"]


@pytest.mark.asyncio
async def test_manager_timeout(manager, tmp_path: Path):
    """Test that dispatch times out for a slow agent."""
    script = tmp_path / "slow_agent.py"
    script.write_text("import time; time.sleep(60)")

    result = await manager.dispatch(
        agent_script=str(script),
        task_id="t-002",
        stage="per_file",
        payload={},
    )
    assert "error" in result or "timeout" in str(result).lower()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_agents_manager.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Write AgentManager implementation**

```python
# src/nano_strix/agents/manager.py
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from nano_strix.agents.base import IPCMessage
from nano_strix.config.schema import IPCConfig


class AgentManager:
    def __init__(self, workspace: Path, config: IPCConfig) -> None:
        self._workspace = workspace
        self._config = config

    async def dispatch(
        self,
        agent_script: str,
        task_id: str,
        stage: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        message = IPCMessage(type="task", task_id=task_id, stage=stage, payload=payload)

        process = await asyncio.create_subprocess_exec(
            "python3", agent_script,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(self._workspace),
        )

        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(message.to_json().encode() + b"\n"),
                timeout=self._config.timeout_seconds,
            )

            if process.returncode != 0:
                return {"error": stderr.decode(errors="replace")}

            output = stdout.decode(errors="replace").strip()
            if not output:
                return {"error": "Agent produced no output"}

            result_msg = IPCMessage.from_json(output)
            return result_msg.payload

        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
            return {"error": f"Agent timed out after {self._config.timeout_seconds}s"}
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/pytest tests/test_agents_manager.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add src/nano_strix/agents/manager.py tests/test_agents_manager.py
git commit -m "feat: add AgentManager for subprocess dispatch"
```

---

## Phase 7: Orchestrator

### Task 10: Pipeline Runner

**Files:**
- Create: `src/nano_strix/orchestrator/__init__.py`
- Create: `src/nano_strix/orchestrator/runner.py`
- Create: `tests/test_orchestrator_runner.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_orchestrator_runner.py
import asyncio
import json
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
from nano_strix.orchestrator.runner import OrchestratorRunner
from nano_strix.config.schema import AppConfig, PipelineConfig
from nano_strix.bus.events import TaskState


@pytest.fixture
def workspace(tmp_path: Path):
    return tmp_path


@pytest.fixture
def runner(workspace):
    return OrchestratorRunner(workspace=workspace, config=AppConfig())


def test_runner_get_stages(runner):
    stages = runner.get_stages(PipelineConfig(stages=["per_file", "report"]))
    assert stages == ["per_file", "report"]


def test_runner_resolve_input(runner, workspace):
    """Test that input_overrides are resolved correctly."""
    findings = workspace / "external_findings.json"
    findings.write_text(json.dumps({"findings": []}))

    result = runner.resolve_input("findings", str(findings))
    assert result is not None


def test_runner_resolve_missing_input(runner):
    result = runner.resolve_input("findings", "/nonexistent/file.json")
    assert result is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_orchestrator_runner.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Write OrchestratorRunner implementation**

```python
# src/nano_strix/orchestrator/__init__.py
```

```python
# src/nano_strix/orchestrator/runner.py
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from nano_strix.config.schema import AppConfig, PipelineConfig


STAGE_SCRIPTS = {
    "per_file": "src/nano_strix/agents/per_file.py",
    "cross_file": "src/nano_strix/agents/cross_file.py",
    "exploit": "src/nano_strix/agents/exploit.py",
}


class OrchestratorRunner:
    def __init__(self, workspace: Path, config: AppConfig) -> None:
        self._workspace = workspace
        self._config = config

    def get_stages(self, pipeline: PipelineConfig) -> list[str]:
        return pipeline.stages

    def resolve_input(self, key: str, path: str) -> dict[str, Any] | None:
        p = Path(path)
        if not p.exists():
            return None
        with open(p) as f:
            return json.load(f)

    def get_stage_script(self, stage: str) -> str | None:
        return STAGE_SCRIPTS.get(stage)
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/pytest tests/test_orchestrator_runner.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add src/nano_strix/orchestrator/ tests/test_orchestrator_runner.py
git commit -m "feat: add OrchestratorRunner with pipeline stage resolution"
```

---

## Phase 8: CLI

### Task 11: CLI 扩展

**Files:**
- Modify: `src/nano_strix/cli.py`
- Modify: `tests/test_cli.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_cli.py
from click.testing import CliRunner
from nano_strix.cli import main


def test_cli_version():
    runner = CliRunner()
    result = runner.invoke(main, ["version"])
    assert result.exit_code == 0
    assert "nano-strix" in result.output


def test_cli_hello():
    runner = CliRunner()
    result = runner.invoke(main, ["hello"])
    assert result.exit_code == 0
    assert "Hello" in result.output


def test_cli_config_init(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    result = runner.invoke(main, ["config", "init"])
    assert result.exit_code == 0


def test_cli_config_show():
    runner = CliRunner()
    result = runner.invoke(main, ["config", "show"])
    assert result.exit_code == 0


def test_cli_run_help():
    runner = CliRunner()
    result = runner.invoke(main, ["run", "--help"])
    assert result.exit_code == 0
    assert "--target" in result.output
    assert "--pipeline" in result.output
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_cli.py -v`
Expected: FAIL — `No such command 'version'` etc.

- [ ] **Step 3: Write CLI implementation**

```python
# src/nano_strix/cli.py
import click
from pathlib import Path

from nano_strix.config.schema import AppConfig
from nano_strix.config.loader import load_config
from nano_strix.config.paths import DEFAULT_CONFIG_PATH, DEFAULT_WORKSPACE_ROOT


@click.group()
@click.version_option(package_name="nano-strix")
def main():
    """nano-strix — LLM-driven penetration testing agent."""


@main.command()
def hello():
    """Say hello."""
    click.echo("Hello from nano-strix!")


@main.command()
def version():
    """Show version info."""
    click.echo("nano-strix 0.1.0")


@main.group()
def config():
    """Configuration management."""


@config.command("init")
def config_init():
    """Generate default config file."""
    DEFAULT_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    import yaml
    cfg = AppConfig()
    with open(DEFAULT_CONFIG_PATH, "w") as f:
        yaml.dump({
            "llm": {"provider": cfg.llm.provider, "model": cfg.llm.model},
            "pipeline": {"stages": cfg.pipeline.stages},
            "ipc": {"timeout_seconds": cfg.ipc.timeout_seconds},
            "logging": {"level": cfg.logging.level},
        }, f, default_flow_style=False)
    click.echo(f"Config written to {DEFAULT_CONFIG_PATH}")


@config.command("show")
def config_show():
    """Show current config."""
    import yaml
    cfg = load_config(DEFAULT_CONFIG_PATH)
    data = {
        "llm": {"provider": cfg.llm.provider, "model": cfg.llm.model},
        "pipeline": {"stages": cfg.pipeline.stages},
        "ipc": {"timeout_seconds": cfg.ipc.timeout_seconds},
        "logging": {"level": cfg.logging.level},
    }
    click.echo(yaml.dump(data, default_flow_style=False))


@main.command()
@click.option("--target", required=True, type=click.Path(exists=True), help="Target code directory")
@click.option("--pipeline", default="full", help="Pipeline preset or comma-separated stages")
@click.option("--input", "input_overrides", multiple=True, help="Input overrides (key=path)")
@click.option("--config", "config_path", type=click.Path(), help="Config file path")
@click.option("--model", help="Override default model")
@click.option("--output", type=click.Path(), help="Output directory")
@click.option("--verbose", is_flag=True, help="Verbose logging")
@click.option("--no-snapshot", is_flag=True, help="Analyze target in-place (no copy)")
def run(target, pipeline, input_overrides, config_path, model, output, verbose, no_snapshot):
    """Run a penetration test pipeline."""
    cfg = load_config(Path(config_path) if config_path else DEFAULT_CONFIG_PATH)

    PIPELINE_PRESETS = {
        "full": ["per_file", "cross_file", "exploit", "report"],
        "analysis": ["per_file", "cross_file", "report"],
        "exploit": ["exploit", "report"],
        "quick": ["per_file", "report"],
    }

    if pipeline in PIPELINE_PRESETS:
        stages = PIPELINE_PRESETS[pipeline]
    else:
        stages = [s.strip() for s in pipeline.split(",")]

    overrides = {}
    for item in input_overrides:
        key, _, path = item.partition("=")
        overrides[key] = path

    click.echo(f"Target: {target}")
    click.echo(f"Pipeline: {' → '.join(stages)}")
    if overrides:
        click.echo(f"Input overrides: {overrides}")
    click.echo("Starting pipeline...")


@main.command()
@click.argument("task_id")
def resume(task_id):
    """Resume an interrupted task."""
    click.echo(f"Resuming task {task_id}...")


@main.command()
@click.argument("task_id")
@click.option("--format", "fmt", default="markdown", type=click.Choice(["markdown", "html", "pdf"]))
def report(task_id, fmt):
    """Regenerate report from existing results."""
    click.echo(f"Generating {fmt} report for task {task_id}...")
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/pytest tests/test_cli.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add src/nano_strix/cli.py tests/test_cli.py
git commit -m "feat: extend CLI with run, resume, report, config commands"
```

---

## Phase 9: 报告生成

### Task 12: Report Generator

**Files:**
- Create: `src/nano_strix/report/__init__.py`
- Create: `src/nano_strix/report/generator.py`
- Create: `src/nano_strix/report/attack_graph.py`
- Create: `src/nano_strix/report/templates/report.md`
- Create: `tests/test_report_generator.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_report_generator.py
from pathlib import Path
from nano_strix.report.generator import ReportGenerator
from nano_strix.report.attack_graph import AttackGraph
from nano_strix.shared.models import Finding


def test_report_generator_generate(tmp_path: Path):
    findings = [
        Finding(
            id="f-001", title="SQL Injection", severity="critical",
            category="sql_injection", file_path="src/auth.py",
            line_range=(42, 58), description="SQLi in login",
            code_snippet="query = f'SELECT * FROM users WHERE id={uid}'",
            recommendation="Use parameterized queries", confidence=0.95,
        ),
        Finding(
            id="f-002", title="XSS", severity="high",
            category="xss", file_path="src/views.py",
            line_range=(10, 20), description="Reflected XSS",
            code_snippet="echo(user_input)",
            recommendation="Sanitize output", confidence=0.8,
        ),
    ]

    gen = ReportGenerator()
    report = gen.generate(findings=findings, target="my-project")
    assert "# 渗透测试报告" in report
    assert "SQL Injection" in report
    assert "critical" in report


def test_attack_graph_build():
    findings = [
        Finding(
            id="f-001", title="SQLi", severity="critical",
            category="sql_injection", file_path="src/auth.py",
            line_range=(42, 58), description="SQLi", code_snippet="",
            recommendation="", confidence=0.9,
        ),
    ]
    graph = AttackGraph(findings)
    mermaid = graph.to_mermaid()
    assert "graph" in mermaid
    assert "f-001" in mermaid
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_report_generator.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Write implementation**

```python
# src/nano_strix/report/__init__.py
```

```python
# src/nano_strix/report/generator.py
from __future__ import annotations

from nano_strix.shared.models import Finding, ExploitResult
from nano_strix.report.attack_graph import AttackGraph


class ReportGenerator:
    def generate(
        self,
        findings: list[Finding],
        target: str,
        exploit_results: list[ExploitResult] | None = None,
    ) -> str:
        sections = []
        sections.append(self._header(target))
        sections.append(self._executive_summary(findings))
        sections.append(self._findings_detail(findings, exploit_results))
        if len(findings) > 1:
            graph = AttackGraph(findings)
            sections.append(f"## 3. 攻击路径图\n\n{graph.to_mermaid()}\n")
        sections.append(self._fix_summary(findings))
        return "\n\n".join(sections)

    def _header(self, target: str) -> str:
        return f"# 渗透测试报告\n\n**目标:** {target}"

    def _executive_summary(self, findings: list[Finding]) -> str:
        severity_counts = {}
        for f in findings:
            severity_counts[f.severity] = severity_counts.get(f.severity, 0) + 1

        lines = ["## 1. 执行摘要\n", "| 严重程度 | 数量 |", "|----------|------|"]
        for sev in ["critical", "high", "medium", "low", "info"]:
            if sev in severity_counts:
                lines.append(f"| {sev} | {severity_counts[sev]} |")
        return "\n".join(lines)

    def _findings_detail(self, findings: list[Finding], exploit_results: list[ExploitResult] | None) -> str:
        verified_map = {}
        if exploit_results:
            for er in exploit_results:
                verified_map[er.finding_id] = er

        lines = ["## 2. 漏洞详情\n"]
        for f in findings:
            lines.append(f"### [{f.severity.upper()}] {f.title}")
            lines.append(f"- **文件:** `{f.file_path}:{f.line_range[0]}-{f.line_range[1]}`")
            lines.append(f"- **置信度:** {f.confidence}")
            lines.append(f"- **描述:** {f.description}")
            lines.append(f"- **代码片段:**\n```python\n{f.code_snippet}\n```")
            if f.id in verified_map:
                er = verified_map[f.id]
                status = "已验证" if er.verified else "未复现"
                lines.append(f"- **漏洞利用验证:** {status}")
                if er.output:
                    lines.append(f"- **验证输出:** {er.output}")
            lines.append(f"- **修复建议:** {f.recommendation}\n")
        return "\n".join(lines)

    def _fix_summary(self, findings: list[Finding]) -> str:
        lines = ["## 4. 修复建议汇总\n", "| 优先级 | 漏洞 | 修复建议 |", "|--------|------|----------|"]
        for i, f in enumerate(findings, 1):
            lines.append(f"| {i} | {f.title} | {f.recommendation} |")
        return "\n".join(lines)
```

```python
# src/nano_strix/report/attack_graph.py
from __future__ import annotations

from nano_strix.shared.models import Finding


class AttackGraph:
    def __init__(self, findings: list[Finding]) -> None:
        self._findings = findings

    def to_mermaid(self) -> str:
        lines = ["graph TD"]
        for f in self._findings:
            node_id = f.id.replace("-", "_")
            severity_tag = f"[{f.severity.upper()}]"
            lines.append(f'    {node_id}["{severity_tag} {f.title}<br/>{f.file_path}:{f.line_range[0]}"]')

        # Chain findings by file dependency (simple heuristic)
        file_groups: dict[str, list[Finding]] = {}
        for f in self._findings:
            file_groups.setdefault(f.file_path, []).append(f)

        prev_node = None
        for f in self._findings:
            node_id = f.id.replace("-", "_")
            if prev_node and f.file_path != self._findings[self._findings.index(f) - 1].file_path:
                prev_id = self._findings[self._findings.index(f) - 1].id.replace("-", "_")
                lines.append(f"    {prev_id} -->|数据流| {node_id}")
            prev_node = f

        return "\n".join(lines)
```

```markdown
<!-- src/nano_strix/report/templates/report.md -->
# 渗透测试报告

**目标:** {{ target }}

## 1. 执行摘要

{{ summary }}

## 2. 漏洞详情

{{ findings }}

## 3. 攻击路径图

{{ attack_graph }}

## 4. 修复建议汇总

{{ fix_summary }}
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/pytest tests/test_report_generator.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add src/nano_strix/report/ tests/test_report_generator.py
git commit -m "feat: add report generator with attack graph"
```

---

## Final: 验证与收尾

- [ ] **Step 1: Run full test suite**

Run: `.venv/bin/pytest -v`
Expected: All tests PASS

- [ ] **Step 2: Run linter**

Run: `.venv/bin/ruff check src/ tests/`
Expected: No errors

- [ ] **Step 3: Run formatter check**

Run: `.venv/bin/ruff format --check src/ tests/`
Expected: All files formatted

- [ ] **Step 4: Verify CLI**

Run: `.venv/bin/nano-strix --help`
Expected: Show all commands (hello, version, run, resume, report, config)

Run: `.venv/bin/nano-strix run --help`
Expected: Show all options (--target, --pipeline, --input, etc.)

- [ ] **Step 5: Final commit**

```bash
git add -A
git commit -m "chore: final cleanup and verification"
```
