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
- LLM 同时为每个文件标注相关维度标签（route/dataflow/auth/dependency），用于 Phase 3 分配子 agent
- 输出: file_manifest.json（写入 task workspace）

### Phase 2: 批量静态扫描

- 自动运行 semgrep（多语言通用扫描）和 bandit（Python 项目）
- 结果写入 manifest 的 `scan_findings` 字段
- 每个文件携带其静态扫描发现的候选漏洞列表
- 此阶段不经过 LLM，纯工具执行

### Phase 3: 多子 Agent 并行分析

- 4 个子 agent，各运行在独立的 `threading.Thread` + 独立 `asyncio` event loop 上
- 共享 file_manifest.json（threading.Lock 保护）
- 共享 LLM 限流器（threading.Semaphore）
- daemon thread，主线程 join 等待全部完成
- 终止条件：`can_finish()` 硬门禁检查通过

## 2. 子 Agent 定义

### Route Agent
- 发现所有 HTTP/API 入口点（Flask routes、FastAPI endpoints、Express routers 等）
- 从 manifest 中筛选 route 相关文件
- 贡献: 路由发现结果写入 manifest，供 Dataflow Agent 使用

### Dataflow Agent
- 追踪用户输入从 source 到 sink 的完整路径
- 依赖 Route Agent 发现的路由信息
- 关注: SQL 查询、命令执行、文件读写、反序列化等危险操作

### Auth Agent
- 分析认证机制、会话管理、鉴权逻辑
- 关注: JWT 验证、session 处理、密码哈希、权限检查中间件

### Dependency Agent
- 分析第三方依赖中的已知漏洞
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

### 数据结构

```python
{
    "files": {
        "src/auth/login.py": {
            "priority": "high",
            "status": "analyzed",           # pending | analyzing | analyzed | skipped
            "assigned_to": "auth_agent",
            "dimensions": ["auth", "dataflow"],
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
    "coverage": {
        "total": 200,
        "high": {"total": 15, "analyzed": 12, "skipped": 0, "pending": 3},
        "medium": {"total": 45, "analyzed": 20, "skipped": 10, "pending": 15},
        "low": {"total": 140, "analyzed": 5, "skipped": 80, "pending": 55}
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

### 状态流转

```
 pending ───────────────────────────────────────────────┐
    │                                                   │
    │  任一 agent 投 "analyze"                              │
    ▼                                                   │
 analyzing ──→ agent 完成分析 ──→ analyzed                │
    │                                                   │
    │  所有 agent 已投票 且 全部投 skip                       │
    ▼                                                   │
 skipped ◄──────────────────────────────────────────────┘

can_finish = True 条件：
  1. 所有 high 文件 status ∈ {analyzed, skipped}
  2. 所有 medium 文件 status ∈ {analyzed, skipped}
  3. 所有 low 文件 status ∈ {analyzed, skipped}
  4. 所有文件的所有 agent skip_votes 均已投出（无 null）
```

### 同步机制

- `threading.Lock` 保护 manifest 读写
- 每个子 agent 读写 manifest 时持有锁，操作完后释放
- `assigned_to` 字段防止两个 agent 同时分析同一文件
- 子 agent 先标记 `assigned_to` + status → `analyzing`，然后释放锁，分析完成后再次获取锁写入 findings

## 4. LLM 集成

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

## 5. 子 Agent 内部结构

### 子 Agent agent_loop 伪代码

```python
def agent_loop(agent_name, manifest, llm_client, semaphore, max_iterations=300):
    """每个子 agent 的核心循环，运行在独立线程的独立 event loop 中"""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    async def _run():
        iteration = 0
        while not manifest.can_finish() and iteration < max_iterations:
            # 1. 从 manifest 选取下一个要分析的文件
            target_file = manifest.claim_pending_file(agent_name)
            if target_file is None:
                # 所有匹配文件都已处理，对剩余不匹配文件投 skip
                manifest.vote_skip_remaining(agent_name)
                continue
            
            # 2. 读取文件内容
            content = read_file(target_file.path)
            scan_results = target_file.scan_findings
            
            # 3. 构建 LLM prompt + 调用 LLM
            semaphore.acquire()
            try:
                response = await llm_client.chat(
                    messages=build_system_prompt(agent_name) + [
                        {"role": "user", "content": f"Analyze: {content}\nScan findings: {scan_results}"}
                    ],
                    tools=TOOLS,
                )
            finally:
                semaphore.release()
            
            # 4. 处理 LLM 响应（可能含 tool calls）
            while response.has_tool_calls:
                tool_results = await execute_tools(response.tool_calls)
                semaphore.acquire()
                try:
                    response = await llm_client.chat(
                        messages=...,
                        tools=TOOLS,
                    )
                finally:
                    semaphore.release()
            
            # 5. 更新 manifest
            manifest.update_file(target_file.path, findings=response.findings, status="analyzed")
            
            iteration += 1
        
        # 超限退出，标记剩余文件为可跳过（附 reason）
        if iteration >= max_iterations:
            manifest.vote_skip_remaining(agent_name, reason="max_iterations reached")
    
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

## 6. IPC 消息协议

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

## 7. 错误处理与恢复

### 子 Agent 故障

- 线程异常被 `threading.excepthook` 捕获
- 故障 agent 负责的文件：`assigned_to` 清空，status 回退 `pending`
- 故障 agent 的未投票文件：保留 null，其他 agent 可接管
- 其他子 agent 继续运行

### 超时控制

- 子 agent 整体超时：`max_iterations`（默认 300）强制退出循环
- LLM 调用超时：LLM 客户端内部超时配置
- 工具执行超时：`terminal_execute` 的 timeout 参数

### Phase 3 整体超时

- 主线程 `thread.join(timeout=phase3_timeout)`
- 超时后：主线程强制收集已完成结果，未完成文件标记为 `skipped`（附带 timeout reason）

## 8. 配置

```yaml
per_file:
  # 子 agent 配置
  agents:
    route_agent: {enabled: true, max_iterations: 300}
    dataflow_agent: {enabled: true, max_iterations: 300}
    auth_agent: {enabled: true, max_iterations: 300}
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
  
  # 静态扫描
  static_scanners:
    - semgrep
    - bandit
```

## 9. 文件变更

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
