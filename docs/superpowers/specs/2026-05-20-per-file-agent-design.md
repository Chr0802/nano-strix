# per_file Agent 实现设计

## 概述

将 per_file agent 从 sleep+log 模拟桩重构为真正的 LLM 驱动逐文件安全分析 agent。采用三阶段分析流程 + Strix 风格的多线程并行子 agent + file_manifest 状态机硬约束，确保对目标代码库的全面覆盖。

## 架构

per_file agent 保持独立脚本模式，由 AgentManager 作为子进程启动（stdin/stdout JSON IPC）。agent 内部使用三阶段流程：单线程发现与分类 → 全量静态扫描 → 多线程子 agent 并行分析，通过共享 file_manifest.json + threading.Lock 实现状态同步，threading.Semaphore 控制 LLM API 并发。

## 1. 三阶段分析流程

### Phase 1: 发现与分类

- 调用 `directory_list` + `file_search` 获取全量文件列表
- LLM 对每个文件打优先级标签：high / medium / low
  - high: auth/login/db/api/route/输入处理/命令执行
  - medium: 业务逻辑、中间件、模型定义
  - low: config/util/static/test/fixtures
- LLM 同时为每个文件标注相关维度标签（route/dataflow/auth/dependency），用于 Phase 3 子 agent 判断各自的处理范围
- 输出: file_manifest.json（写入 task workspace），phase = "classification"

### Phase 2: 批量静态扫描

- 自动运行 semgrep（多语言通用扫描）和 bandit（Python 项目）
- 结果写入 manifest 的 `scan_findings` 字段
- 每个文件携带其静态扫描发现的候选漏洞列表
- 此阶段不经过 LLM，纯工具执行
- 完成后 manifest.phase = "static_scan"

### Phase 3: 多子 Agent 并行分析

- 4 个子 agent，各运行在独立的 `threading.Thread` + 独立 `asyncio` event loop 上
- 共享 file_manifest.json（threading.Lock 保护）
- 共享 LLM 限流器（threading.Semaphore）
- daemon thread，主线程 join 等待全部完成
- 终止条件：`can_finish()` 硬门禁检查通过
- manifest.phase = "analysis"

## 2. 子 Agent 定义

### Route Agent
- 发现所有 HTTP/API 入口点（Flask routes、FastAPI endpoints、Express routers 等）
- 从 manifest 中筛选维度标签含 `"route"` 的文件
- 将发现的路由写入 `discovered_routes`，供 Dataflow Agent 使用

### Dataflow Agent
- 追踪用户输入从 source 到 sink 的完整路径
- 从 manifest 中筛选维度标签含 `"dataflow"` 的文件
- 读取 `discovered_routes` 作为追踪起点线索
- 关注: SQL 查询、命令执行、文件读写、反序列化等危险操作

### Auth Agent
- 分析认证机制、会话管理、鉴权逻辑
- 从 manifest 中筛选维度标签含 `"auth"` 的文件
- 关注: JWT 验证、session 处理、密码哈希、权限检查中间件

### Dependency Agent
- 分析第三方依赖中的已知漏洞
- 从 manifest 中筛选维度标签含 `"dependency"` 的文件
- 检查 requirements.txt、package.json、pom.xml 等依赖声明
- 与 CVE 数据库交叉引用

### 子 Agent 协作规则

| 规则 | 描述 |
|------|------|
| 全票 skip | 文件状态变为 `skipped` 需要每个子 agent 都明确投 `skip` |
| 一票否决 | 任一子 agent 投 `analyze`，文件进入 `analyzing` 状态 |
| 未投票 | 子 agent 尚未处理该文件，status 保持 `pending` |
| 故障回退 | 子 agent 崩溃/超时，其未投票文件回退为 `pending`，其他 agent 可接管 |
| 跳过理由 | 投 `skip` 必须附带 `skip_reason`，写入 manifest 供审计 |

## 3. file_manifest.json

manifest 是 per_file agent 的核心共享状态，同时承载"文件覆盖追踪"和"子 agent 断点"两层职责。

### 数据结构

```python
{
    "phase": "analysis",               # classification | static_scan | analysis

    # ══════════ 子 Agent 维度的断点状态 ══════════
    "agents_state": {
        "route_agent": {
            "status": "running",        # running | completed | crashed | restarted
            "thread_id": 140234567890,  # 当前线程 ID，崩溃后比对检测
            "restart_count": 0,         # 已重启次数
            "current_file": "src/api/handler.py",  # 当前分析文件，崩溃后定位孤儿
            "iteration": 42,            # 当前迭代轮次，恢复后从此值继续
            "files_analyzed": 15,
            "files_skipped": 30,
            "last_health_check": "2026-05-20T10:30:15Z"
        },
        "dataflow_agent": {
            "status": "running",
            "thread_id": 140234567891,
            "restart_count": 0,
            "current_file": "src/db/query.py",
            "iteration": 38,
            "files_analyzed": 12,
            "files_skipped": 28,
            "last_health_check": "2026-05-20T10:30:15Z"
        },
        "auth_agent": {
            "status": "crashed",
            "thread_id": null,
            "restart_count": 1,
            "current_file": null,
            "iteration": 25,
            "files_analyzed": 8,
            "files_skipped": 36,
            "last_health_check": "2026-05-20T10:25:00Z",
            "crash_reason": "LLM API connection reset"
        },
        "dependency_agent": {...}
    },

    # ══════════ 共享数据：路由发现结果 ══════════
    "discovered_routes": [              # Route Agent 写入，Dataflow Agent 消费
        {"path": "/api/login", "method": "POST", "file": "src/auth/login.py", "line": 42},
        {"path": "/api/users",  "method": "GET",  "file": "src/api/users.py",  "line": 15}
    ],

    # ══════════ 文件维度的协作状态 ══════════
    "files": {
        "src/auth/login.py": {
            "priority": "high",
            "status": "analyzed",           # pending | analyzing | analyzed | skipped
            "assigned_to": "auth_agent",
            "dimensions": ["auth", "dataflow", "route"],
            "retry_count": 0,
            "analyzing_started_at": "2026-05-20T10:30:15Z",
            "scan_findings": [
                {"rule": "sql-injection", "line": 45, "severity": "high"}
            ],
            "skip_votes": {
                "route_agent": "analyze",
                "dataflow_agent": "analyze",
                "auth_agent": "analyze",
                "dependency_agent": "skip"
            },
            "findings": [
                {
                    "id": "F-001",
                    "title": "SQL Injection in login handler",
                    "severity": "critical",
                    "category": "sql_injection",
                    "file_path": "src/auth/login.py",
                    "line_range": [44, 48],
                    "description": "User input passed directly to SQL query",
                    "code_snippet": "...",
                    "recommendation": "Use parameterized queries",
                    "confidence": 0.95
                }
            ]
        },
        "src/utils/format.py": {
            "priority": "low",
            "status": "skipped",
            "assigned_to": null,
            "dimensions": [],
            "retry_count": 0,
            "analyzing_started_at": null,
            "scan_findings": [],
            "skip_votes": {
                "route_agent": "skip",
                "dataflow_agent": "skip",
                "auth_agent": "skip",
                "dependency_agent": "skip"
            },
            "skip_reason": "route_agent: no routes; dataflow_agent: pure format; auth_agent: no auth; dependency_agent: no deps",
            "findings": []
        }
    },

    # ══════════ 汇总统计 ══════════
    "coverage": {
        "total": 200,
        "high":   {"total": 15, "analyzed": 12, "skipped": 0,  "pending": 3},
        "medium": {"total": 45, "analyzed": 20, "skipped": 10, "pending": 15},
        "low":    {"total": 140,"analyzed": 5,  "skipped": 80, "pending": 55}
    },
    "hard_gate": {
        "can_finish": false,
        "blocked_by": [
            "src/admin/dashboard.py: pending (high, unvoted by auth_agent, dependency_agent)",
            "src/api/middleware.py: pending (high, assigned to route_agent, analyzing)"
        ]
    }
}
```

### agents_state 各字段说明

| 字段 | 用途 |
|------|------|
| `status` | `running` / `completed` / `crashed` / `restarted` |
| `thread_id` | 当前 OS 线程 ID，主线程巡检时比对，不匹配则判定崩溃 |
| `restart_count` | 重启次数，超过 `max_agent_restarts` 不再重启 |
| `current_file` | 当前正在分析的文件路径，崩溃后用于定位孤儿文件 |
| `iteration` | 当前迭代轮次，崩溃恢复后新 agent 从此值继续计数 |
| `files_analyzed` / `files_skipped` | 进度统计 |
| `last_health_check` | 最近一次健康心跳时间戳，巡检程序据此判定假死 |

### 断点恢复流程（使用 agents_state）

```
主线程发现 route_agent 线程终止
       │
       ▼
读取 agents_state["route_agent"]
       │
       ├── status = "running" 但 thread 已死 → 确认崩溃
       ├── current_file = "src/api/handler.py" → 定位孤儿
       ├── restart_count = 0 → 未超 max_agent_restarts，可以重启
       │
       ▼
清理孤儿文件:
  files["src/api/handler.py"] → status = "pending", assigned_to = null, retry_count += 1
  所有 assigned_to = "route_agent" 且 status != "analyzed" → assigned_to = null
       │
       ▼
更新 agents_state["route_agent"]:
  status = "restarted"
  restart_count = 1
  current_file = null
  thread_id = <新线程 ID>
  iteration = 42  (保留原计数继续)
       │
       ▼
创建新线程 → 新 route_agent 从 manifest 取 pending 文件开始工作
```

### 状态流转

```
 pending ─────────────────────────────────────────────────────────────┐
    │                                                                 │
    │  任一 agent 投 "analyze"                                          │
    ▼                                                                 │
 analyzing ──→ agent 完成分析 ──→ analyzed                              │
    │         retry_count += 1                                         │
    │         (崩溃/超时)                                                │
    │         ├── retry_count <= max_file_retries → 回退 pending         │
    │         └── retry_count > max_file_retries → 强制 skipped          │
    │                                                                 │
    │  所有 agent 已投票 且 全部投 skip                                   │
    ▼                                                                 │
 skipped ◄────────────────────────────────────────────────────────────┘

can_finish = True 条件：
  1. 所有 high 文件 status ∈ {analyzed, skipped}
  2. 所有 medium 文件 status ∈ {analyzed, skipped}
  3. 所有 low 文件 status ∈ {analyzed, skipped}
  4. 所有文件的所有 agent skip_votes 均已投出（无 null）
  5. 无 analyzing 孤儿文件
```

### 同步机制

- `threading.Lock` 保护 manifest 读写
- 每个子 agent 读写 manifest 时持有锁，操作完后释放
- `assigned_to` 字段防止两个 agent 同时分析同一文件
- 子 agent 先标记 `assigned_to` + status → `analyzing` + `analyzing_started_at` + 更新 `agents_state.current_file`，然后释放锁。分析完成后再次获取锁写入 findings + 更新 `agents_state`
- 主线程巡检程序定时读取 manifest，检测孤儿文件和假死 agent（详见 7.2）

### Manifest 持久化

- manifest 变更后即时写入 task workspace 的 `file_manifest.json`
- 进程崩溃重启后从文件恢复完整状态（包括 agents_state）
- `phase` 字段指示当前阶段，重启后跳过已完成阶段

## 4. 子 Agent 文件选择策略

核心问题：每个子 agent 有特定的分析维度，必须能判断"哪些文件该我来分析、先处理哪个、哪些不归我管"。

### 4.1 维度匹配

Phase 1 分类时 LLM 为每个文件标注 `dimensions` 列表。子 agent 根据自己的维度过滤：

| 子 Agent | 关注维度 | 匹配条件 |
|----------|---------|---------|
| route_agent | `"route"` | 文件包含 HTTP 路由、API 端点定义 |
| dataflow_agent | `"dataflow"` | 文件包含用户输入处理、数据库操作、命令执行 |
| auth_agent | `"auth"` | 文件包含认证、鉴权、会话管理 |
| dependency_agent | `"dependency"` | 依赖声明文件 或 import 第三方库的文件 |

### 4.2 claim_pending_file 选择逻辑

```python
def claim_pending_file(agent_name, manifest):
    """
    子 agent 选择下一个要分析的文件。
    返回 None 表示该 agent 所有文件均已投票。
    """
    my_dimension = AGENT_DIMENSIONS[agent_name]  # e.g., "auth"

    # Step 1: 候选过滤 — pending 且该 agent 尚未投票且未被占用
    candidates = []
    for path, f in manifest.files.items():
        if agent_name in f["skip_votes"] and f["skip_votes"][agent_name] is not None:
            continue  # 已投票
        if f["status"] == "analyzing" and f["assigned_to"] is not None:
            continue  # 其他 agent 正在分析
        if f["retry_count"] > manifest.max_file_retries:
            continue  # 超过重试上限
        candidates.append((path, f))

    if not candidates:
        return None  # 所有文件都已投票

    # Step 2: 排序 — 维度匹配优先 > 风险等级 > 路径字母序
    def sort_key(item):
        path, f = item
        dimension_match = 1 if my_dimension in f["dimensions"] else 0
        priority_order = {"high": 0, "medium": 1, "low": 2}
        return (-dimension_match, priority_order.get(f["priority"], 2), path)

    candidates.sort(key=sort_key)
    return candidates[0]
```

### 4.3 排序优先级

```
第1优先级: 维度匹配（匹配本 agent 维度的文件优先）
第2优先级: 风险等级（high → medium → low）
第3优先级: 文件路径（字母序，保证确定性）

示例 — auth_agent 的选择顺序：
  src/auth/login.py      (dim=match, priority=high)   ← 先分析
  src/auth/middleware.py  (dim=match, priority=high)
  src/api/admin.py       (dim=match, priority=medium)
  src/db/user.py         (dim=no match, priority=high) ← 维度不匹配，投 skip
  src/utils/format.py    (dim=no match, priority=low)  ← 维度不匹配，投 skip
```

### 4.4 不匹配文件的处理

当 agent 拿到一个不匹配自己维度的文件时：

1. **不分析内容** — 不属于它的专业领域
2. **投 skip 票** — 附带 `skip_reason`（如 "dataflow_agent: no user input or dangerous sink"）
3. **快速释放** — 不消耗 LLM 调用，直接写 manifest

### 4.5 子 Agent 间数据依赖

Route Agent 和 Dataflow Agent 存在轻量依赖：Dataflow 知道路由入口点后能更精准追踪数据流。但不阻塞并行：

```
Route Agent                          Dataflow Agent
    │                                    │
    │ 分析 src/api/login.py               │ claim_pending_file()
    │ 发现路由: POST /api/login (L42)      │ 拿到 src/db/query.py
    │                                    │
    │ 写入 discovered_routes              │ 读文件 + LLM 分析（独立发现 source）
    │                                    │ 同时读取 discovered_routes 作为提示
    │                                    │
    ▼                                    ▼
  下一文件                             下一文件
```

- Route Agent 发现的每条路由写入 `discovered_routes`
- Dataflow Agent 每轮开始前读取 `discovered_routes`，作为追踪数据流的起点线索
- Dataflow Agent 也可通过 LLM 阅读代码独立发现 source（`request.get()` 等），**不强依赖**
- 即使 Route Agent 还没产出，Dataflow 也能工作

### 4.6 投票生命周期

```
Agent 启动 → agents_state[agent_name].status = "running"
    │
    ▼
claim_pending_file()
    │
    ├── 返回 (path, file) → 分配到文件
    │   │
    │   ├── 维度匹配 → 分析文件 → 投 "analyze" 票 → 写入 findings
    │   └── 维度不匹配 → 投 "skip" 票（附 reason）→ 快速释放
    │
    └── 返回 None → 所有文件已投票 → agent_finish
            │
            ▼
         agents_state[agent_name].status = "completed"
```

## 5. LLM 集成

### 各阶段模型

```yaml
llm:
  models:
    per_file: claude-haiku-4-5-20251001   # Phase 1 分类（大量文件，快速处理）
    per_file_analysis: claude-sonnet-4-6  # Phase 3 子 agent 深度分析
```

### LLM 限流

- `threading.Semaphore(max_concurrent)` 控制跨线程总 LLM 并发数
- 默认 max_concurrent = 4（可配置）
- 子 agent 调用 LLM 前 acquire semaphore，完成后 release

## 6. 子 Agent 内部结构

### 子 Agent agent_loop 伪代码

```python
def agent_loop(agent_name, manifest, llm_client, semaphore, max_iterations=300):
    """每个子 agent 的核心循环，运行在独立线程的独立 event loop 中"""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    async def _run():
        iteration = manifest.agents_state[agent_name]["iteration"]  # 断点恢复

        while not manifest.can_finish() and iteration < max_iterations:
            # 1. 读取共享数据（如 Dataflow 读 discovered_routes）
            hints = manifest.get_hints(agent_name)

            # 2. 选取下一个要分析的文件
            target = manifest.claim_pending_file(agent_name)
            if target is None:
                manifest.vote_skip_remaining(agent_name)
                manifest.update_agent_state(agent_name, {"iteration": iteration})
                continue

            # 3. 标记文件为 analyzing + 更新 agent 断点
            manifest.mark_analyzing(target.path, agent_name)
            manifest.update_agent_state(agent_name, {
                "current_file": target.path,
                "iteration": iteration,
                "last_health_check": now_iso()
            })

            try:
                # 4. 分析文件
                content = read_file(target.path)
                messages = build_prompt(agent_name, content, target.scan_findings, hints)

                semaphore.acquire()
                try:
                    response = await llm_client.chat(messages, tools=TOOLS)
                finally:
                    semaphore.release()

                # 5. 处理 tool calls 循环
                while response.has_tool_calls:
                    tool_results = await execute_tools(response.tool_calls)
                    semaphore.acquire()
                    try:
                        response = await llm_client.chat(
                            build_followup(response, tool_results),
                            tools=TOOLS,
                        )
                    finally:
                        semaphore.release()

                # 6. 成功 → 写入 findings
                manifest.update_file(target.path, findings=response.findings, status="analyzed")
                manifest.update_agent_state(agent_name, {
                    "files_analyzed": manifest.agents_state[agent_name]["files_analyzed"] + 1
                })

            except Exception:
                # 7. 异常 → 回退文件状态，不阻塞其他文件
                manifest.handle_agent_error(target.path, agent_name)

            iteration += 1

        # 超限退出
        if iteration >= max_iterations:
            manifest.vote_skip_remaining(agent_name, reason="max_iterations reached")

        manifest.update_agent_state(agent_name, {"status": "completed"})

    loop.run_until_complete(_run())
    loop.close()
```

### 工具集

```python
TOOLS = [
    "file_read",          # 读取文件内容
    "file_search",        # 搜索文件/模式
    "directory_list",     # 列目录
    "terminal_execute",   # 运行 semgrep/bandit/trufflehog 等工具
    "create_finding",     # 创建一个 Finding 记录
    "vote_skip",          # 对文件投 skip 票
    "check_manifest",     # 查看当前 manifest 状态
]
```

## 7. IPC 消息协议

### 输入（stdin，来自 AgentManager）

```json
{
    "type": "task",
    "task_id": "t-001",
    "stage": "per_file",
    "payload": {
        "target": "/workspace/t-001/source",
        "stage_results": {}
    }
}
```

### 输出（stdout，返回给 AgentManager）

```json
{
    "type": "result",
    "task_id": "t-001",
    "payload": {
        "status": "ok",
        "stage": "per_file",
        "target": "/workspace/t-001/source",
        "findings": [
            {
                "id": "F-001",
                "title": "SQL Injection in login handler",
                "severity": "critical",
                "category": "sql_injection",
                "file_path": "src/auth/login.py",
                "line_range": [44, 48],
                "description": "User input passed directly to SQL query",
                "code_snippet": "...",
                "recommendation": "Use parameterized queries",
                "confidence": 0.95
            }
        ],
        "file_manifest": { ... },
        "coverage_summary": {
            "total_files": 200,
            "high_analyzed": 15,
            "medium_analyzed": 40,
            "low_analyzed": 100,
            "skipped": 45
        }
    }
}
```

### 进度回报（stdout，可选）

```json
{
    "type": "progress",
    "task_id": "t-001",
    "payload": {
        "phase": "phase3",
        "analyzed_count": 120,
        "total_high_remaining": 2,
        "current_agent": "auth_agent"
    }
}
```

## 8. 错误处理与断点恢复

### 8.1 子 Agent 故障与自动重试

```
子 Agent 异常退出
       │
       ▼
主线程检测到 thread 终止
       │
       ▼
读取 agents_state[agent_name]
       │
       ├── status = "running" 但 thread 已死 → 确认崩溃
       ├── current_file = "src/api/handler.py" → 定位孤儿
       ├── restart_count → 是否超 max_agent_restarts
       │
       ▼
清理孤儿文件:
  current_file → retry_count += 1
    ├── <= max_file_retries → status = "pending", assigned_to = null
    └── >  max_file_retries → status = "skipped" (reason: "max retries exceeded")
  其他 assigned_to = 该 agent 的 pending 文件 → assigned_to = null
       │
       ▼
更新 agents_state:
  status = "restarted"
  restart_count += 1
  current_file = null
  thread_id = <新线程 ID>
       │
       ▼
创建新线程 → 新 agent 从 manifest 取 pending 文件继续工作
```

### 8.2 孤儿文件检测（agent 假死）

- 每个文件有 `analyzing_started_at` 时间戳
- 每个 agent 有 `last_health_check` 心跳时间戳
- 主线程定时巡检：
  - `status = "analyzing"` 且 `now - analyzing_started_at > orphan_timeout_seconds` → 孤儿文件
  - `agents_state[agent].status = "running"` 且 `now - last_health_check > orphan_timeout_seconds` → 假死 agent
- 孤儿文件处理：`assigned_to` 清空，status → `pending`，`retry_count += 1`
- 假死 agent：累计 unhealthy_count 超过阈值 → 强制终止线程 → 走 8.1 流程重启

### 8.3 完整进程崩溃恢复

per_file agent 子进程本身崩溃时，StageScheduler 的 stage 级重试机制触发。per_file agent 重新启动后：

1. 读取 task workspace 中的 `file_manifest.json`（包含 agents_state）
2. 检测 manifest 中的 `phase` 字段：
   - `phase = "classification"` → 从头开始
   - `phase = "static_scan"` → 从 Phase 2 继续
   - `phase = "analysis"` → 直接进入 Phase 3
3. 检查 `agents_state`：将所有 `status = "running"` 的 agent 标记为 `"crashed"`，按 8.1 流程清理孤儿文件
4. Phase 3 重新创建四个子 agent 线程，从 manifest 中的 `pending` 文件开始工作
5. 已在 manifest 中的 `analyzed` / `skipped` 文件结果保留

### 8.4 超时控制

- 子 agent 整体超时：`max_iterations`（默认 300）强制退出循环
- LLM 调用超时：LLM 客户端内部超时配置
- 工具执行超时：`terminal_execute` 的 timeout 参数

### 8.5 Phase 3 整体超时

- 主线程 `thread.join(timeout=phase3_timeout)`
- 超时后：主线程强制收集已完成结果
- 超时剩余未分析文件：
  - high → 标记 `skipped`（reason: "phase3 timeout"），记录在 manifest 供审计
  - medium/low → 标记 `skipped`（reason: "phase3 timeout"）

## 9. 配置

```yaml
per_file:
  # 子 agent 配置
  agents:
    route_agent:      {enabled: true, max_iterations: 300}
    dataflow_agent:   {enabled: true, max_iterations: 300}
    auth_agent:       {enabled: true, max_iterations: 300}
    dependency_agent: {enabled: true, max_iterations: 300}

  # LLM 配置
  llm:
    classification_model: claude-haiku-4-5-20251001
    analysis_model: claude-sonnet-4-6
    max_concurrent: 4
    max_tokens: 4096
    temperature: 0.1

  # 超时配置
  phase3_timeout_seconds: 1800
  per_file_timeout_seconds: 3600

  # 断点恢复与重试
  max_file_retries: 3               # 单文件最大分析重试次数
  orphan_timeout_seconds: 600        # 文件 analyzing 状态超时判定孤儿
  max_agent_restarts: 3             # 单类子 agent 最多重启次数
  manifest_sync_interval_seconds: 5  # manifest 同步到磁盘间隔
  health_check_interval_seconds: 30  # 主线程巡检间隔

  # 静态扫描
  static_scanners:
    - semgrep
    - bandit
```

## 10. 文件变更

| 操作 | 文件 |
|------|------|
| 重写 | `src/nano_strix/agents/per_file.py` |
| 新建 | `src/nano_strix/agents/per_file/manifest.py` — Manifest 数据结构和同步逻辑 |
| 新建 | `src/nano_strix/agents/per_file/sub_agents.py` — 子 agent 定义和 agent_loop |
| 新建 | `src/nano_strix/agents/per_file/classifier.py` — Phase 1 文件分类 |
| 新建 | `src/nano_strix/agents/per_file/static_scanner.py` — Phase 2 静态扫描 |
| 修改 | `src/nano_strix/config/schema.py` — 添加 PerFileConfig |
| 新建 | `tests/test_per_file_agent.py` |
| 新建 | `tests/test_per_file_manifest.py` |
