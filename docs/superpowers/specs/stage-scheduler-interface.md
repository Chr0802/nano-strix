# Stage 与调度器接口规范

> 本文档定义 nano-strix 中 Stage（各阶段 agent 脚本）与 StageScheduler（调度器）之间的
> 接口契约，包括通信协议、TaskState 状态机、事件流、输出规范和日志标准。

---

## 1. 架构概览

```
CLI (cli.py)
 └─ _execute_pipeline()
     ├─ EventBus      ← 任务状态持久化 (state.json / events.jsonl)
     ├─ AgentManager  ← 子进程管理 (dispatch / IPC)
     └─ StageScheduler ← 流水线编排 (submit / run / retry)
          │
          ├─ Queue: per_file ──→ AgentManager.dispatch(per_file.py)
          │    └─ 产出: file_manifest.json + findings
          ├─ Queue: cross_file ─→ AgentManager.dispatch(cross_file.py)
          │    └─ 消费: per_file 的 stage_results
          ├─ Queue: exploit  ──→ AgentManager.dispatch(exploit.py)
          │    └─ 消费: cross_file 的 stage_results
          └─ Queue: report   ──→ AgentManager.dispatch(report.py)
               └─ 消费: 所有前序 stage 的 stage_results
```

**关键组件职责：**

| 组件               | 职责                                                 |
| ------------------ | ---------------------------------------------------- |
| `StageScheduler` | 管理流水线队列、并发控制、重试逻辑、状态推进         |
| `EventBus`       | 任务状态（state.json）和事件流（events.jsonl）的读写 |
| `AgentManager`   | 子进程启动、stdin/stdout IPC、超时控制               |
| Stage 脚本         | 接收任务 JSON → 执行分析 → 输出结果 JSON           |

---

## 2. Stage 脚本注册

所有 stage 脚本在 `src/nano_strix/orchestrator/runner.py` 的 `STAGE_SCRIPTS` 中注册：

```python
STAGE_SCRIPTS = {
    "per_file":    "src/nano_strix/agents/per_file.py",
    "cross_file":  "src/nano_strix/agents/cross_file.py",
    "exploit":     "src/nano_strix/agents/exploit.py",
    "report":      "src/nano_strix/agents/report.py",
}
```

**添加新 stage 的步骤：**

1. 在 `STAGE_SCRIPTS` 中注册脚本路径
2. 在 `config.schema.SchedulerConfig.stages` 中添加默认并发配置
3. 在 `cli.py` 的 `pipeline_presets` 中按需加入流水线预设
4. 实现 stage 脚本（遵循下文接口规范）

---

## 3. IPC 通信协议

Stage 脚本作为**独立子进程**启动，通过 stdin/stdout/stderr 三通道与 `AgentManager` 通信。

### 3.1 Stdin —— 任务输入

AgentManager 通过 stdin 发送一行 JSON：

```json
{
  "type": "task",
  "task_id": "t-a1b2c3d4",
  "stage": "per_file",
  "payload": {
    "target": "/path/to/target/source",
    "stage_results": {
      "per_file": { "findings": [...], "manifest_path": "..." }
    }
  }
}
```

**`IPCMessage` 字段：**

| 字段                      | 类型       | 必填 | 说明                                     |
| ------------------------- | ---------- | ---- | ---------------------------------------- |
| `type`                  | `string` | 是   | 消息类型：`task`                       |
| `task_id`               | `string` | 是   | 格式 `t-{8位hex}`，全局唯一            |
| `stage`                 | `string` | 否   | 当前 stage 名称                          |
| `payload.target`        | `string` | 是   | 分析目标路径（绝对路径）                 |
| `payload.stage_results` | `dict`   | 否   | 前序 stage 的输出结果，key 为 stage 名称 |

### 3.2 Stdout —— 结果输出（IPC 通道）

Stage 脚本在 stdout 输出**恰好一行 JSON**，作为该 stage 的最终结果：

```json
{
  "type": "result",
  "task_id": "t-a1b2c3d4",
  "payload": {
    "status": "ok",
    "stage": "per_file",
    "target": "/path/to/target",
    "findings": [
      {
        "id": "F-001",
        "title": "SQL Injection in login handler",
        "severity": "critical",
	"exploitability": "E0",
	"nature": "A1",
        "category": "sqli",
        "file_path": "src/auth/login.py",
        "line_range": [42, 45],
        "description": "User input passed directly to SQL query",
        "code_snippet": "cursor.execute(f\"SELECT * FROM users WHERE name='{user}'\")",
        "recommendation": "Use parameterized queries",
        "confidence": 0.95
      }
    ],
    "coverage_summary": { "total": 12, "high": {...} },
    "manifest_path": "/workspace/tasks/t-a1b2c3d4/file_manifest.json",
    "timings": { "phase1_classification": 2.3, "phase2_static_scan": 5.1, "phase3_analysis": 45.2 }
  }
}
```

**Stdout 结果规范：**

- 必须是**恰好一行**合法 JSON
- `type` 固定为 `"result"`
- `payload.status` 为 `"ok"` 表示成功，`"error"` 表示失败
- 失败时 `payload.error` 包含错误描述
- `payload` 中除 `status` 外的字段**由各 stage 自定义**，会被存入 `state.stage_results[stage_name]`

### 3.3 Stderr —— 日志与进度通道

Stderr 承载两类输出：

**A. 结构化进度消息（JSON 行）：**

```json
{"type": "progress", "task_id": "t-a1b2c3d4", "payload": {"phase": "phase1_complete", "total_files": 42, "elapsed_s": 2.3}}
```

- `type` 为 `"progress"`
- `payload.phase` 标识当前阶段（如 `phase1_complete`、`phase2_complete`）
- 进度消息由 `AgentManager` 以 `logger.debug` 记录

**B. 普通日志文本：**

由 Python `logging` 模块通过 `StreamHandler(stderr)` 输出，遵循第 7 节的日志规范。

---

## 4. TaskState 状态机

### 4.1 状态定义

```
                    ┌──────────┐
                    │ pending  │  ← 任务创建时的初始状态
                    └────┬─────┘
                         │ advance(stage)
                         ▼
                    ┌──────────┐
              ┌─────│ running  │─────┐
              │     └────┬─────┘     │
              │          │           │
              │  complete_stage()   fail()
              │          │           │
              │          ▼           ▼
              │     ┌──────────┐ ┌──────────┐
              │     │ running  │ │  failed  │  ← 不可恢复
              │     │(下一stage)│ └──────────┘
              │     └────┬─────┘
              │          │
              │  所有 stage 完成
              │          │
              │          ▼
              │     ┌────────────┐
              └─────│ completed  │
                    └────────────┘
```

### 4.2 TaskState 数据结构

存储在 `<workspace>/tasks/<task_id>/state.json`：

```json
{
  "task_id": "t-a1b2c3d4",
  "stages": ["per_file", "cross_file", "exploit", "report"],
  "current_stage": "cross_file",
  "status": "running",
  "stage_results": {
    "per_file": {
      "status": "ok",
      "findings": [...],
      "coverage_summary": {...},
      "manifest_path": "..."
    }
  },
  "error": null,
  "retry_counts": {}
}
```

### 4.3 状态转换规则

| 转换                     | 触发条件                            | 副作用                                                    |
| ------------------------ | ----------------------------------- | --------------------------------------------------------- |
| `pending → running`   | `advance(stage)` 被调用           | `current_stage` 更新，`state.json` 写入               |
| `running → running`   | `complete_stage()` 且有下一 stage | `stage_results[stage]` 写入，`current_stage` 清空     |
| `running → completed` | `complete_stage()` 且为最后 stage | `status` 更新，`task_completed` 事件发布              |
| `running → failed`    | `fail(error)` 被调用              | `status` 更新，`error` 写入，`task_failed` 事件发布 |
| `completed → running` | `resume_task()`                   | 用于断点恢复，仅当存在未完成的 stage                      |

### 4.4 断点恢复

`resume_task()` 从第一个未在 `stage_results` 中出现的 stage 重新开始：

```python
for stage in self._stages:
    if stage not in state.stage_results:
        # 从此 stage 恢复
        self._queues[stage].put((task_id, target_path))
        return
```

**Stage 脚本必须满足的条件才能支持断点恢复：**

- 从 `payload.stage_results` 读取前序 stage 的产出
- 将自身状态写入 `<workspace>/<task_id>/` 下的持久化文件（如 `file_manifest.json`）
- stdout 结果幂等：相同输入重复执行应产生一致结果

---

## 5. 事件流（events.jsonl）

每个任务有独立的事件流文件 `<workspace>/tasks/<task_id>/events.jsonl`，每行一个 JSON。

### 5.1 事件格式

```json
{"task_id": "t-a1b2c3d4", "event_type": "stage_started", "stage": "per_file", "payload": {}, "timestamp": "2026-05-21T10:30:00.123456+00:00"}
```

### 5.2 事件类型

| event_type          | 发布时机                   | stage        | payload                           |
| ------------------- | -------------------------- | ------------ | --------------------------------- |
| `task_created`    | `submit_task()`          | `null`     | `{"target": "/path/to/target"}` |
| `stage_started`   | 开始执行某个 stage         | stage 名     | `{}`                            |
| `stage_completed` | stage 执行成功             | stage 名     | stage 脚本的 stdout payload       |
| `task_completed`  | 所有 stage 完成            | `null`     | `{}`                            |
| `task_failed`     | stage 执行失败（重试耗尽） | 失败的 stage | `{"error": "错误描述"}`         |

### 5.3 事件消费

事件的**唯一写入者**是 `StageScheduler`。外部工具（CLI、监控面板）可以读取 `events.jsonl` 来追踪任务进度。Stage 脚本**不直接写入** events.jsonl。

---

## 6. Stage 输出规范

### 6.1 通用要求

每个 stage 的 stdout 结果 `payload` 必须包含：

| 字段       | 类型       | 必填 | 说明                        |
| ---------- | ---------- | ---- | --------------------------- |
| `status` | `string` | 是   | `"ok"` 或 `"error"`     |
| `stage`  | `string` | 是   | 当前 stage 名称（用于校验） |
| `target` | `string` | 是   | 分析目标路径                |

错误时的额外字段：

| 字段      | 类型       | 必填 | 说明               |
| --------- | ---------- | ---- | ------------------ |
| `error` | `string` | 是   | 人类可读的错误描述 |

### 6.2 per_file stage 特有字段

| 字段                 | 类型       | 说明                                                                                                                          |
| -------------------- | ---------- | ----------------------------------------------------------------------------------------------------------------------------- |
| `findings`         | `array`  | Finding 对象数组，每个包含 id/title/severity/category/file_path/line_range/description/code_snippet/recommendation/confidence |
| `coverage_summary` | `object` | 文件覆盖统计：total + 按 priority 分组 (high/medium/low × analyzed/skipped/pending)                                          |
| `manifest_path`    | `string` | `file_manifest.json` 的绝对路径                                                                                             |
| `timings`          | `object` | 各阶段耗时：phase1_classification / phase2_static_scan / phase3_analysis（秒）                                                |

### 6.3 Finding 对象标准格式

```json
{
  "id": "F-001",
  "title": "简短描述",
  "severity": "critical|high|medium|low|info",
  "category": "sqli|xss|rce|auth|...",
  "file_path": "相对路径",
  "line_range": [起始行, 结束行],
  "description": "漏洞详细描述",
  "code_snippet": "相关代码片段",
  "recommendation": "修复建议",
  "confidence": 0.95
}
```

### 6.4 stage_results 传递规范

后续 stage 通过 `payload.stage_results` 接收前序 stage 的输出：

```python
# cross_file.py 中读取 per_file 的产出
per_file_result = payload.get("stage_results", {}).get("per_file", {})
findings = per_file_result.get("findings", [])
manifest_path = per_file_result.get("manifest_path")
```

**注意：** `stage_results` 只包含成功的 stage。如果 per_file 失败，整个流水线会在此终止，cross_file 不会被执行。

---

## 7. 日志规范

### 7.1 日志配置来源

- **CLI 进程：** `cli.py` 的 `_execute_pipeline()` 从 `config.yaml` 的 `logging.level` 读取级别，`--verbose` 覆盖为 DEBUG
- **Stage 子进程：** 调用 `nano_strix.logging.setup.setup_logging(cfg.logging)`，从全局 `~/.nano-strix/config.yaml` 读取

### 7.2 日志格式

统一格式（由 `setup_logging` 配置）：

```
HH:MM:SS [模块路径] 级别 消息内容
```

示例：

```
10:30:01 [nano_strix.agents.per_file] INFO --- Phase 1: Classification ---
10:30:01 [per_file_lib.classifier] INFO Phase 1: discovered 42 files in /target (0.05s)
10:30:03 [per_file_lib.classifier] INFO Phase 1: LLM response received in 2.10s (tokens: in=1500 out=800)
10:30:03 [per_file_lib.classifier] INFO Phase 1: classification high=5 medium=12 low=25  dims={'auth': 3, 'route': 5}
```

### 7.3 日志输出位置

| 进程             | Stderr                       | 日志文件                               |
| ---------------- | ---------------------------- | -------------------------------------- |
| CLI (`cli.py`) | 通过 `logging.basicConfig` | 无（由调用者决定）                     |
| per_file         | 格式化日志 + JSON 进度行     | `<workspace>/<task_id>/per_file.log` |
| cross_file       | 格式化日志                   | 无（使用 stderr）                      |
| exploit          | 格式化日志                   | 无（使用 stderr）                      |
| report           | 格式化日志                   | 无（使用 stderr）                      |

### 7.4 日志级别使用约定

| 级别        | 使用场景                                                                           |
| ----------- | ---------------------------------------------------------------------------------- |
| `DEBUG`   | 详细调试信息：LLM 原始请求/响应内容、scanner 命令行、文件跳过决策                  |
| `INFO`    | 关键状态变更：阶段开始/完成、文件发现数量、LLM 调用耗时、findings 数量、agent 完成 |
| `WARNING` | 可恢复异常：LLM JSON 解析失败（降级为默认值）、tool 未安装、agent crash 重试、超时 |
| `ERROR`   | 不可恢复错误：agent 子进程 crash 且超过最大重试次数、LLM provider 初始化失败       |

### 7.5 敏感信息

**绝对禁止**在日志中输出：

- API Key / Auth Token
- 完整的 base_url（如包含 token）
- 用户源代码全文（可输出文件名和行号）

### 7.6 必须记录的日志点

每个 stage 脚本**必须**记录以下事件（使用 INFO 级别）：

1. **启动**：task_id、target 路径、关键配置（model、max_concurrent、scanners）
2. **每个子阶段开始/结束**：阶段名、耗时、关键统计
3. **LLM 调用**：model 名、prompt 大小、响应耗时、token 用量
4. **错误/异常**：完整 traceback（`logger.exception`）

---

## 8. 中间产物目录结构

Stage 脚本将中间产物输出到 `<workspace>/<task_id>/` 下：

```
workspace/
└── tasks/
    └── t-a1b2c3d4/
        ├── state.json                 ← EventBus 写入
        ├── events.jsonl               ← EventBus 写入
        ├── source/                    ← 分析目标（被调度器复制到此处）
        ├── per_file.log               ← per_file 日志
        ├── per_file_run_meta.json     ← 运行元数据（timings, model, coverage）
        ├── file_manifest.json         ← 文件清单（跨阶段持久化）
        ├── phase1_classification/     ← 阶段 1 中间产物
        │   ├── discovered_files.json
        │   ├── llm_prompt.json
        │   ├── llm_response_raw.txt
        │   └── classification_result.json
        ├── phase2_static_scan/        ← 阶段 2 中间产物
        │   ├── semgrep/
        │   │   ├── stdout.json
        │   │   ├── stderr.txt
        │   │   └── meta.json
        │   ├── bandit/
        │   │   ├── stdout.json
        │   │   ├── stderr.txt
        │   │   └── meta.json
        │   └── summary.json
        └── phase3_analysis/           ← 阶段 3 中间产物
            ├── route_agent/
            │   └── src_auth_login.py/
            │       ├── llm_prompt.json
            │       ├── llm_response_raw.txt
            │       ├── llm_meta.json
            │       └── findings.json
            ├── dataflow_agent/
            ├── auth_agent/
            └── dependency_agent/
```

---

## 9. 调度器并发与重试

### 9.1 并发配置

在 `config.yaml` 的 `scheduler.stages` 中配置：

```yaml
scheduler:
  stages:
    per_file:
      max_concurrent: 2   # 同时最多 2 个 per_file worker
      max_retries: 2      # 失败后最多重试 2 次
    cross_file:
      max_concurrent: 1
      max_retries: 2
    exploit:
      max_concurrent: 1
      max_retries: 2
    report:
      max_concurrent: 1
      max_retries: 0
```

### 9.2 Worker 模型

每个 stage 启动 `max_concurrent` 个 asyncio worker，从该 stage 的队列中拉取任务执行。同一 stage 的多个 worker **并发执行不同任务**。

### 9.3 重试机制

```
for attempt in range(max_retries + 1):
    result = await agent_manager.dispatch(...)
    if "error" not in result:
        return result  # 成功
    # 失败，记录并重试
state.fail(error_msg)  # 重试耗尽，标记失败
```

### 9.4 超时控制

- **IPC 超时：** `config.ipc.timeout_seconds`（默认 300s），由 AgentManager 控制
- **Stage 内部超时：** 各 stage 自行管理（如 per_file 的 `phase3_timeout_seconds`）
- 超时后 AgentManager 发送 SIGKILL 并返回 `{"error": "Agent timed out after Ns"}`

---

## 10. 错误处理流程

```
Stage 脚本异常
    │
    ▼
AgentManager.dispatch() 返回 {"error": "..."}
    │
    ▼
StageScheduler._execute_stage()
    │
    ├─ 重试次数 < max_retries → 重新 dispatch
    │
    └─ 重试耗尽
        │
        ▼
    state.fail(error_msg)
        │
        ▼
    EventBus.publish(task_failed)
        │
        ▼
    scheduler._mark_done()
        │
        ▼
    任务终止，后续 stage 不再执行
```

**关键约定：**

- Stage 脚本内部应捕获所有异常，通过 stdout 返回 `{"status": "error", "error": "..."}`
- 未捕获的异常导致子进程非零退出码时，stderr 内容作为错误信息返回
- 重试是**整体重试**（整个 stage 重新执行），不是增量恢复

---

## 11. 实现检查清单

新 stage 脚本接入时必须满足：

- [ ] 从 stdin 读取一行 JSON（`IPCMessage` 格式）
- [ ] 解析 `task_id` 和 `payload.target`
- [ ] 通过 `payload.stage_results` 读取前序 stage 输出
- [ ] 调用 `setup_logging()` 配置日志
- [ ] 在 stdout 输出恰好一行 JSON（`type: "result"`）
- [ ] `payload.status` 为 `"ok"` 或 `"error"`
- [ ] 将所有中间产物写入 `<workspace>/<task_id>/` 下
- [ ] 异常时通过 stdout 返回错误（不依赖子进程退出码）
- [ ] 支持幂等执行（`state.json` + `stage_results` 可用于断点恢复）
- [ ] 日志输出到 stderr，包含至少：启动、每阶段耗时、LLM 调用统计、错误详情

---

## 12. 参考实现

- **完整 stage 实现：** `src/nano_strix/agents/per_file.py` — 多阶段、持久化中间产物、完整日志
- **简单 stage 桩：** `src/nano_strix/agents/cross_file.py` — 最小可工作的 stage 模板
- **调度器：** `src/nano_strix/orchestrator/scheduler.py` — `StageScheduler`
- **IPC 管理：** `src/nano_strix/agents/manager.py` — `AgentManager.dispatch()`
- **状态模型：** `src/nano_strix/bus/events.py` — `TaskState` / `TaskEvent`
- **日志工具：** `src/nano_strix/logging/setup.py` — `setup_logging()`
