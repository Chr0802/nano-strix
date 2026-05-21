# Deep Analysis Agent Graph 重构设计

## 概述

将 per_file stage 从固定三阶段流水线（分类→静态扫描→多 agent 并行分析）重构为基于 Strix
agents_graph 模式的递归式 root-agent/sub-agent 架构。同时将 per_file 和 cross_file 合并为
统一的 "deep-analysis" stage，由 DeepAnalyseAgent 作为 agent 基类。

## 1. 整体架构

```
deep_analysis.py (子进程，StageScheduler 视角不变)
│
└── RootAgent (单线程，主 event loop)
    │
    │  tools: create_agent / send_message / wait_for_message / view_agent_graph
    │          + read_manifest / check_coverage / merge_manifest
    │
    ├── Phase 1: spawn ClassifyAgent (文件分类)
    │   ├── 文件数 <= N? → 直接分类
    │   └── 文件数 > N?  → spawn 多个 ClassifyAgent → wait_for_message → merge
    │
    ├── Phase 2: spawn ScanAgent (静态扫描 via Docker sandbox)
    │   └── wait_for_message() 等待完成
    │
    ├── Phase 3: spawn AnalyzeAgent (逐文件深度分析)
    │   └── 按维度+文件量决定拆分策略 → spawn 子 agent → wait_for_message → merge
    │
    ├── Phase 4: spawn CrossLinkAgent (多文件关联分析)
    │   └── wait_for_message() 等待完成
    │
    ├── Phase 5: spawn ReviewAgent (结果复核与精炼)
    │   ├── 对所有 findings 做去重、交叉验证、消除假阳性
    │   └── wait_for_message() 等待完成
    │
    └── 汇总结果 → stdout (给 StageScheduler)
```

- **Phase 序列固定**：classify → scan → per-file → cross-link → review，但每 phase 内部可递归拆分
- **不再有硬编码函数调用**：root agent 是 LLM agent，通过 create_agent/wait_for_message 驱动
- **递归拆分**：每 phase 的 agent 根据任务量决定直接执行还是 spawn 多个子 agent
- **层级化 Manifest**：每层 agent 维护局部 manifest，父 agent 合并子 agent 结果
- **外部接口不变**：StageScheduler → AgentManager → stdin/stdout JSON IPC 保持不变

## 2. Agent Graph 基础设施

新增 `agents/per_file_lib/graph.py`，与 Strix 的 `agents_graph_actions.py` 保持严格一致。

### 2.1 全局状态

```python
_agent_graph: dict[str, Any] = {
    "nodes": {},     # agent_id -> {name, task, status, parent_id, role, created_at, ...}
    "edges": [],     # [{from, to, type: "delegation"|"message"}, ...]
}
_root_agent_id: str | None = None
_agent_messages: dict[str, list[dict[str, Any]]] = {}   # per-agent inbox
_running_agents: dict[str, threading.Thread] = {}        # active daemon threads
_agent_instances: dict[str, Any] = {}                    # live agent objects
_agent_states: dict[str, Any] = {}                       # state references
```

### 2.2 五个核心原语

作为 `@register_tool` 暴露给 LLM agent，与 strix 签名保持一致：

| Tool | 签名 | 关键行为 |
|------|------|---------|
| `create_agent` | `(task, name, inherit_context=True)` | 实例化 DeepAnalyseAgent → daemon thread → delegation edge → 注入 `<agent_delegation>` XML + 继承 context |
| `send_message_to_agent` | `(target_agent_id, message, message_type, priority)` | 推消息到 `_agent_messages[target]` → message edge → wake_event.set() |
| `wait_for_message` | `(reason)` | `agent_state.enter_waiting_state()` → graph node status="waiting" |
| `agent_finish` | `(result_summary, findings, success, report_to_parent)` | 生成 `<agent_completion_report>` XML → 推入父 inbox → 子 agent status="finished" |
| `view_agent_graph` | `()` | 递归遍历 delegation edges 构建 ASCII 树 |

### 2.3 AgentState

```python
@dataclass
class AgentState:
    agent_id: str
    agent_name: str
    parent_id: str | None
    task: str
    role: str                         # classify / scan / analyze / cross-link / review
    messages: list[dict[str, Any]]    # conversation history
    iteration: int = 0
    max_iterations: int = 300
    completed: bool = False
    stop_requested: bool = False
    waiting_for_input: bool = False
    waiting_timeout: int = 600
    final_result: dict | None = None
    _wake_event: asyncio.Event        # field(default_factory=asyncio.Event)

    def enter_waiting_state(self): ...
    def resume_from_waiting(self): ...
    async def wait_for_wake(self, timeout=0.5): ...
```

### 2.4 Agent 生命周期

与 strix `BaseAgent.agent_loop()` 完全一致：

```
agent_loop:
  while True:
    1. _check_agent_messages()    → consume inbox → resume if waiting
    2. if waiting_for_input: await wait_for_wake(0.5); continue
    3. if should_stop(): return
    4. LLM iteration → tool execution
    5. if agent_finish called: break → return final_result
```

### 2.5 与 strix 的关键差异

| 方面 | strix | nano-strix |
|------|-------|------------|
| LLM 调用 | `self.llm.generate()` + XML tool format | `LLMProvider.chat()` + Anthropic native tool calling |
| Tool 格式 | `<function=tool_name><parameter=p>v</parameter></function>` | 标准 Anthropic/OpenAI function calling |
| Prompt 模板 | Jinja2 | Python `string.Template` 轻量模板 |
| Sandbox | Docker 容器 | Docker 容器（仅在 ScanAgent phase 使用） |

## 3. DeepAnalyseAgent 基类

### 3.1 类型体系

```
DeepAnalyseAgent (base, 进程内线程运行)
  ├── RootAgent        — 调度 phase 序列，管理 manifest 覆盖率
  ├── ClassifyAgent    — Phase 1: 文件分类
  ├── ScanAgent        — Phase 2: 静态扫描 (Docker sandbox)
  ├── AnalyzeAgent     — Phase 3: 逐文件深度分析
  ├── CrossLinkAgent   — Phase 4: 多文件关联分析
  └── ReviewAgent      — Phase 5: 结果复核与精炼
```

### 3.2 Prompt 模板（统一模板 + 参数化角色）

使用 Python `string.Template` 实现轻量模板：

```
system_prompt = Template("""
You are $role_name, a specialized security analysis agent.
Your task domain: $role_description

<core_capabilities>
$capabilities
</core_capabilities>

<communication_rules>
- Work autonomously on your assigned task
- Use agent_finish when complete to report back to parent
- NEVER send empty messages — use wait_for_message if idle
- You are a SPECIALIST — focus exclusively on your delegated task
</communication_rules>

<agent_graph_tools>
These tools let you coordinate:
- create_agent: spawn sub-agents for parallel work
- send_message_to_agent: communicate with sibling agents
- wait_for_message: pause until sub-agents complete
- agent_finish: report results to parent
- view_agent_graph: view current agent tree structure
</agent_graph_tools>

<analysis_tools>
$tool_descriptions
</analysis_tools>

<output_format>
Return findings as JSON array. Each finding has: id, title, severity,
category, file_path, line_range, description, code_snippet, recommendation, confidence.
If no issues found, return empty findings list.
</output_format>
""")
```

### 3.3 工具集按角色分配

| 角色 | 可用工具 |
|------|---------|
| RootAgent | create_agent, wait_for_message, view_agent_graph, read_manifest, check_coverage, merge_manifest |
| ClassifyAgent | file_search, file_read, directory_list, create_agent, agent_finish |
| ScanAgent | tool_server_execute (Docker sandbox), create_agent, agent_finish |
| AnalyzeAgent | file_read, file_search, directory_list, load_skill, create_agent, agent_finish |
| CrossLinkAgent | file_read, file_search, load_skill, read_manifest, create_agent, agent_finish |
| ReviewAgent | read_manifest, file_read, load_skill, create_agent, agent_finish |

## 4. LLM 多协议支持

### 4.1 架构

```
LLMProvider (ABC, 不变)
  ├── AnthropicProvider       (已有)
  └── OpenAICompatibleProvider (新增)
```

### 4.2 OpenAICompatibleProvider

新增 `llm/openai_compatible.py`，实现 `LLMProvider` ABC：

- 使用 `openai` Python SDK 的 `AsyncOpenAI` 客户端
- OpenAPI 的 system message → messages 中的 `role: "system"` 条目
- Tool call 映射：OpenAI `tool_calls` → 内部 `ToolCall` 格式
- Stop reason 映射：`stop` → `"stop"`, `tool_calls` → `"tool_calls"`
- 支持 OpenAI、DeepSeek、及其他 OpenAI-compatible API

### 4.3 上层无感

Agent 代码全部通过 `LLMProvider` ABC 和 `LLMResponse` / `ToolCall` 交互，无感底层协议差异。

## 5. Docker Sandbox + Tool Server

### 5.1 架构

```
per_file 进程
  │
  ├── Agent Graph (线程内)
  │     └── ScanAgent → tool_server_execute("semgrep ...")
  │
  └── SandboxManager
        └── DockerSandbox
              └── Tool Server (容器内 HTTP REST API)
                    ├── POST /tools/terminal_execute
                    ├── POST /tools/file_read
                    └── POST /tools/scanner/*
```

### 5.2 DockerSandbox

基于 `sandbox/docker.py` 骨架实现：

- **镜像**：`nano-strix-sandbox:latest`，预装 semgrep、bandit、gitleaks 等
- **生命周期**：create_sandbox() → agent 交互 → destroy()
- **网络**：默认 `network="none"`（安全隔离）
- **Volume**：tasks/{task_id}/source/ → 容器 /workspace/source/（只读）

### 5.3 Tool Server

容器内轻量 HTTP 服务：

| 端点 | 功能 |
|------|------|
| POST /tools/terminal_execute | 执行 shell 命令（30s 超时），返回 stdout/stderr/exit_code |
| POST /tools/file_read | 读取容器内文件 |
| POST /tools/scanner/semgrep | 运行 semgrep，返回 JSON |
| POST /tools/scanner/bandit | 运行 bandit，返回 JSON |

### 5.4 tool_server_execute 工具封装

```python
@register_tool
def tool_server_execute(agent_state, tool_name: str, arguments: dict) -> dict:
    """Execute a tool inside the Docker sandbox."""
    sandbox = get_sandbox_for_agent(agent_state.agent_id)
    return await sandbox.call_tool_server(tool_name, arguments)
```

## 6. Skills 技能加载系统

### 6.1 设计

技能是特定漏洞类型的 Markdown 知识文件，在构建时由 SkillLoader 加载到内存。Agent 通过 `load_skill(skill_name)` 工具按需获取。

### 6.2 技能目录结构

```
src/nano_strix/skills/
  ├── __init__.py
  ├── loader.py              # SkillLoader: 扫描目录, 加载到 dict
  ├── sql_injection.md
  ├── xss.md
  ├── auth_jwt.md
  ├── ssrf.md
  ├── rce.md
  └── ...
```

### 6.3 API

```python
class SkillLoader:
    def __init__(self, skills_dir: Path): ...
    def load_all(self) -> dict[str, str]: ...    # skill_name -> markdown content
    def get_skill(self, name: str) -> str: ...   # 获取单个技能内容
    def list_skills(self) -> list[str]: ...      # 列出所有可用技能名

@register_tool
def load_skill(agent_state, skill_name: str) -> dict:
    """Load a vulnerability-specific skill guide for the agent."""
    content = skill_loader.get_skill(skill_name)
    agent_state.add_message("user", f"<specialized_knowledge>\n{content}\n</specialized_knowledge>")
    return {"success": True, "skill": skill_name}
```

## 7. 层级化 Manifest

### 7.1 结构

```
RootAgent
  └── manifest (root, 全局)
        ├── Phase 1: ClassifyAgent → local_manifest_1 (分类结果)
        ├── Phase 2: ScanAgent → local_manifest_2 (扫描 findings)
        ├── Phase 3: AnalyzeAgent → local_manifest_3 (深度分析)
        │     ├── SubAnalyzeAgent-A → local_manifest_3a
        │     └── SubAnalyzeAgent-B → local_manifest_3b  (merge → 3)
        ├── Phase 4: CrossLinkAgent → local_manifest_4 (跨文件关联)
        └── Phase 5: ReviewAgent → local_manifest_5 (复核精炼 findings)
```

### 7.2 规则

- **局部 manifest 独立性**：每个 agent 只读写自己的局部 manifest，不触碰父级
- **Merge 由父 agent 执行**：收到子 agent 的 `<agent_completion_report>` 后调用 merge_manifest()
- **覆盖检测由 root agent 执行**：check_coverage() 检查 root manifest 中所有文件状态
- **序列化**：manifest 通过 `<agent_completion_report>` 中的 JSON block 传递

### 7.3 现有 manifest.py 适配

保留核心数据结构（ManifestFile, FileManifest），增加：
- to_dict() / from_dict() — 序列化传输
- merge(other) — 合并子 manifest
- 移除直接写 file_manifest.json 的逻辑（持久化由 root agent 统一管理）

## 8. IPC 接口（外部不变）

### 8.1 输入

```json
{
    "type": "task",
    "task_id": "t-001",
    "stage": "deep_analysis",
    "payload": {
        "target": "/workspace/t-001/source",
        "stage_results": {}
    }
}
```

### 8.2 输出

```json
{
    "type": "result",
    "task_id": "t-001",
    "payload": {
        "status": "ok",
        "stage": "deep_analysis",
        "target": "/workspace/t-001/source",
        "findings": [...],
        "coverage_summary": {...},
        "manifest_path": "/workspace/tasks/t-001/file_manifest.json",
        "timings": {
            "phase1_classification": 2.3,
            "phase2_static_scan": 5.1,
            "phase3_per_file_analysis": 45.2,
            "phase4_cross_link": 12.0,
            "phase5_review": 8.5
        }
    }
}
```

## 9. 文件变更

| 操作 | 文件 | 说明 |
|------|------|------|
| 重写 | `agents/per_file.py` → `agents/deep_analysis.py` | 新 stage 入口，启动 RootAgent |
| 新增 | `agents/per_file_lib/graph.py` | Agent graph 基础设施，与 strix 对齐 |
| 新增 | `agents/per_file_lib/deep_agent.py` | DeepAnalyseAgent 基类 + 6 种 agent 子类 |
| 新增 | `agents/per_file_lib/prompts.py` | 统一 prompt 模板 + 角色参数 |
| 重写 | `agents/per_file_lib/manifest.py` | 增加 to_dict/from_dict/merge |
| 新增 | `llm/openai_compatible.py` | OpenAI-compatible provider |
| 新增 | `sandbox/tool_server.py` | Docker 容器内 HTTP 工具服务 |
| 重写 | `sandbox/docker.py` | DockerSandbox 完整实现 |
| 新增 | `skills/` | 技能加载系统和知识文件 |
| 修改 | `config/schema.py` | 新增 DeepAnalysisConfig, SkillsConfig, SandboxConfig 增强 |
| 修改 | `orchestrator/runner.py` | STAGE_SCRIPTS: per_file → deep_analysis, 移除 cross_file |
| 删除 | `agents/cross_file.py` | 合并入 deep_analysis stage |

## 10. 拆分判定逻辑（混合模式）

- **框架默认策略**：文件数 > N 自动按子目录拆分；每个子 agent 分配上限阈值
- **LLM override**：agent 可通过 create_agent 的 task 参数指定自定义拆分策略
- **判定执行者**：agent 内部的 `_should_split(file_count, complexity)` 方法返回框架建议，但 LLM agent 可通过直接调用 create_agent 来 override

## 11. 与 Strix 的完整对齐对照

| 概念 | strix | nano-strix |
|------|-------|------------|
| Graph 全局状态 | `_agent_graph`, `_agent_messages`, `_running_agents`, `_agent_instances`, `_agent_states` | 完全相同 |
| Root 标记 | `_root_agent_id` | 完全相同 |
| Edge 类型 | delegation, message | 完全相同 |
| Message schema | id, from, to, content, message_type, priority, timestamp, delivered, read | 完全相同 |
| Agent 基类 | BaseAgent + AgentMeta metaclass | DeepAnalyseAgent（简化，无 metaclass） |
| AgentState | Pydantic BaseModel + PrivateAttr(_wake_event) | dataclass + _wake_event |
| 核心循环 | agent_loop: check_messages → wait → iterate → tools → repeat | 完全相同 |
| context 继承 | inherited_context_from_parent + agent_delegation XML | 完全相同 |
| 完成报告 | agent_completion_report XML | 完全相同 |
| Prompt 模板 | Jinja2 | string.Template |
| Tool 调用 | XML function=格式 | Anthropic/OpenAI native function calling |
