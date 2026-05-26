# 深度分析阶段 Harness 机制 — 设计方案

**日期**: 2026-05-26
**状态**: Spec

## 背景与目标

五阶段深度分析流水线（分类→扫描→逐文件分析→跨文件关联→审查）由 LLM 驱动的 RootAgent 编排执行，各阶段 agent 通过 `create_agent`、`agent_finish` 等图工具完成协调。当前缺失机制保证：

- 每个阶段的输入数据在 agent 启动前已就绪
- 每个阶段的交付物满足结构化质量要求
- 各阶段进度可追踪

本设计引入 **harness 机制**：在图工具层植入检查钩子，对 LLM 透明地校验每个阶段的输入和交付物，不满足时触发自动重试，超限则上报失败。

## 设计决策

| 维度 | 决定 |
|------|------|
| 模式 | 主动把关 — 校验不通过则阻断进入下一阶段 |
| 粒度 | 结构化校验 — JSON Schema 验证字段完整性 |
| 失败处理 | 自动重试（最多 3 次），超限升级给调用方 |
| 覆盖范围 | 全五阶段（classify/scan/analyze/cross-link/review）+ RootAgent 最终交付 |
| 集成方式 | 图中间件 hook — `create_agent` pre-hook + `agent_finish` post-hook |

## 核心组件

### 1. StageContract（阶段契约）

定义每个阶段的输入前置条件和输出 schema，纯 Python 数据类，确定性执行。

```python
@dataclass
class StageContract:
    stage_name: str
    input_predicates: list[InputPredicate]   # 输入前置条件列表
    output_schema: dict                      # JSON Schema，描述交付物必须字段
    max_retries: int = 3
    retry_prompt_template: str              # 校验失败时注入给 agent 的修复提示

@dataclass
class InputPredicate:
    description: str                        # 人类可读描述
    check: Callable[[Path], bool]           # 接收 workspace_root，返回 T/F
```

**各阶段契约摘要：**

| 阶段 | 输入前置条件 | 输出关键字段 |
|------|-------------|-------------|
| classify | 项目中至少存在一个源码文件 | `file_manifest.json`，每个条目含 `path`, `language`, `classification` |
| scan | `file_manifest.json` 存在且含已分类文件 | 每个文件含 `vulnerability_type`, `severity`, `location`, `description` |
| analyze | 每个待分析文件存在对应扫描结果 | `findings[]`，每个含 `file`, `vulnerability_type`, `severity`, `line_range`, `description`, `exploitability` |
| cross-link | `findings` 来自多个文件均已分析完毕 | `cross_findings[]`，每个含 `related_findings`, `relation_type`, `combined_severity` |
| review | 所有阶段交付物完整 | `final_report` 含 `executive_summary`, `findings[]`, `recommendations[]`, `coverage_report` |
| root_final | 所有五阶段 marked completed | 所有阶段交付物齐备 + 全局覆盖度 ≥ 阈值 |

### 2. HarnessHooks（钩子注册与执行）

在 `graph.py` 的 `create_agent()` 和 `agent_finish()` 中植入检查点。

```python
_HOOKS: dict[str, list[Callable]] = {
    "pre_create_agent": [],
    "post_agent_finish": [],
    "pre_root_finish": [],
}
```

**create_agent pre-hook 流程：**

1. 任意 agent 调用 `create_agent(name="<stage>", task="...")`
2. pre-hook 根据 `name` 查找对应 `StageContract`
3. 执行 `input_predicates` 所有检查
4. 全部通过 → 正常创建 agent，`StageState[stage].status → in_progress`
5. 任一失败 → 返回错误消息给**调用方 agent**，不创建子 agent

**agent_finish post-hook 流程：**

1. agent 调用 `agent_finish(result_summary="...", findings=[...])`
2. post-hook 用 `output_schema` 校验 `findings` 结构
3. 全部通过 → 正常完成，`StageState[stage].status → completed`
4. 校验失败 → 重试计数 +1：
   - 未超限：返回错误消息给 agent，agent 继续 `agent_loop` 修正后重新调用 `agent_finish`
   - 超限：`StageState[stage].status → failed`，通知调用方 agent

**关键设计点：**

- 钩子失败不抛异常，返回描述性错误消息供 LLM 读取并据此修正
- 重试计数在 harness 内存中维护，不依赖 LLM 记忆
- pre-hook 和 post-hook 同步执行（同一线程），无需额外并发控制
- 调用方不限于 RootAgent——AnalyzeAgent、CrossLinkAgent 等创建子 agent 时同样触发校验

**并行子 agent 的处理：** 一个阶段可能有多个并行子 agent（如 per-file analyzer 拆分），每个独立通过 post-hook 校验。仅当该阶段所有 agent 均 completed 后，StageState 才标记为 completed。

### 3. StageState（阶段进度状态）

模块级状态追踪表，补充现有 `_agent_graph` 等全局状态。

```python
_stage_states: dict[str, StageProgress] = {}  # key: stage_name

@dataclass
class StageProgress:
    stage_name: str
    status: StageStatus          # pending | in_progress | validating | completed | failed
    agent_ids: list[str]         # 该阶段当前活跃 agent ID 列表
    retry_counts: dict[str, int] # agent_id → 当前重试次数
    started_at: float | None
    completed_at: float | None
    last_checkpoint: str         # 最近一次钩子检查的描述
    artifacts: list[str]         # 该阶段产出的文件路径列表
```

**状态转换：**

```
pending → in_progress → validating → completed
                     ↘ validating → in_progress (重试)
                     ↘ failed (超过最大重试)
```

### 4. 日志增强

扩展现有 `GraphLogger` 的事件字段：

- `agent_status_change` 增加 `stage_name`、`checkpoint_detail` 字段
- `agent_finished` 增加 `validation_result`（passed/failed/retry_exhausted）、`schema_errors` 字段

`view_agent_graph` 工具返回内容增加 `_stage_states`，使 RootAgent 可通过此工具了解各阶段进度。

## 数据流示例

以 scanner 阶段为例，完整生命周期：

```
1. RootAgent 调用 create_agent(name="static_scanner", task="...")
   │
   ├─ [pre-hook] StageContract("scan").check_input(workspace)
   │     ├─ file_manifest.json 存在? ✅
   │     ├─ manifest 中有已分类文件? ✅
   │     └─ 通过 → 创建 ScanAgent, StageState["scan"] → in_progress
   │
2. ScanAgent 执行完毕，调用 agent_finish(result_summary="...", findings=[...])
   │
   ├─ [post-hook] validate_output(findings)
   │     ├─ findings 是 list? ✅
   │     ├─ 每个含 vulnerability_type, severity, location? ✅
   │     └─ 通过 → StageState["scan"] → completed
   │
   # 或失败路径:
   ├─ [post-hook] 校验失败
   │     ├─ retry_count = 1 (≤ max_retries)
   │     ├─ 返回错误: "findings[3] 缺少 severity 字段"
   │     ├─ ScanAgent 继续 agent_loop 修正
   │     └─ ScanAgent 重新调用 agent_finish → 再次 post-hook
   │
   # 或耗尽路径:
   ├─ [post-hook] 第 4 次校验失败
   │     ├─ retry_count = 4 (> max_retries)
   │     ├─ StageState["scan"] → failed
   │     └─ 错误升级给 RootAgent
```

## 文件变更

### 新增文件

| 文件 | 用途 |
|------|------|
| `agents/deep_analysis_lib/contracts.py` | `StageContract`、`InputPredicate` 数据类 + 五阶段契约定义 |
| `agents/deep_analysis_lib/hooks.py` | `HarnessHooks` — 钩子注册、执行调度、重试管理 |
| `agents/deep_analysis_lib/stage_state.py` | `StageProgress`、`StageStatus` + `_stage_states` 全局管理 |
| `tests/test_harness_contracts.py` | StageContract 校验逻辑单元测试 |
| `tests/test_harness_hooks.py` | HarnessHooks 与图工具交互集成测试 |

### 修改文件

| 文件 | 改动 |
|------|------|
| `agents/deep_analysis_lib/graph.py` | `create_agent()` 中调用 pre-hook；`agent_finish()` 中调用 post-hook；`view_agent_graph()` 返回 stage_states；`_HOOKS` 注册表 |
| `agents/deep_analysis.py` | 入口点初始化 harness，注册 hooks |
| `logging/graph_logger.py` | `agent_status_change` 和 `agent_finished` 增加 harness 相关字段 |

## 测试策略

### 单元测试 — StageContract 校验逻辑

- 各阶段 input_predicates 通过/失败场景
- 各阶段 output_schema 校验通过/部分字段缺失/类型错误场景
- 空 workspace、残缺 manifest、缺失阶段产物的边界条件
- max_retries 耗尽后状态正确标记为 failed

### 集成测试 — HarnessHooks 与图工具交互

- `create_agent` 触发 pre-hook 且正确阻断/放行
- `agent_finish` 触发 post-hook 且返回具体错误消息
- 并行子 agent 各自独立校验、聚合完成判断
- 阶段状态全生命周期转换（pending→completed, pending→failed）
- 重试一次通过、重试耗尽两种路径

### 端到端测试

- 构造含已知漏洞的测试仓库，运行完整五阶段流水线，验证全链路 stage_state 追踪正确、最终交付物 schema 通过
- 注入阶段交付物不完整的情况，验证重试→修复→通过

### 已有测试兼容

- 现有 `test_deep_analysis_stage_integration.py` 中涉及 `create_agent`/`agent_finish` 的断言需根据钩子返回格式调整
- 钩子通过注册机制挂载，测试中可注册 mock hook 隔离

## 风险与约束

- **LLM 理解错误消息的能力：** 校验失败时返回给 agent 的错误消息需结构化、精确定位问题（字段名、索引、缺少项），使 LLM 能据此修正。不依赖 LLM 自行推断。
- **同步钩子对延迟的影响：** input_predicates 仅做文件系统检查（ms 级），output_schema 校验为纯内存 JSON Schema 验证（μs 级），不会显著增加阶段间延迟。
- **不侵入 prompts：** harness 逻辑完全在图工具层运行，无需修改任何 agent prompt。Hook 返回的错误消息作为工具返回值自然流入 LLM 上下文中。
