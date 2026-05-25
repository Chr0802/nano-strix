# 深度分析阶段日志记录 — 设计方案

**日期**: 2026-05-25
**状态**: Spec

## 背景与目标

当前 deep analysis agent 仅使用 Python 标准库 `logging` 输出诊断信息，
调试 agent 行为时需要观察三个维度的数据：LLM 请求/响应、工具调用、以及
agent graph 状态变化。代码库中已存在 `LLMLogger`、`ToolLogger` 等结构化
JSONL 日志组件，但未集成到 deep analysis agent 中。

本设计的目标是将上述三类日志写入 `{task_workspace}/logs/` 目录，
按类别分文件存储，且日志写入失败绝不能影响 agent 主流程。

## 输出文件

| 文件 | Category | 内容 |
|---|---|---|
| `llm.jsonl` | `llm` | LLM 请求完整 messages 和响应完整 content |
| `tools.jsonl` | `tool` | 工具调用的完整参数和完整返回值 |
| `graph.jsonl` | `graph` | Agent graph 事件（创建、状态变更、消息传递、完成） |

## 组件变更

### 修改的文件

| 文件 | 改动 |
|---|---|
| `logging/llm_logger.py` | 新增 `log_request_full()`、`log_response_full()`，记录完整消息体和响应 |
| `logging/tool_logger.py` | 增强 `log_execution()`，完整记录 `arguments` 和 `result` |
| `agents/deep_analysis_lib/deep_agent.py` | 构造函数接收 loggers；在 `_process_iteration()` 中调用 |
| `agents/deep_analysis_lib/graph.py` | 核心函数接收 `GraphLogger` 参数并调用 |
| `agents/deep_analysis.py` | 入口点创建 logger 实例，注入 agent 和 graph 函数 |

### 新增文件

| 文件 | 用途 |
|---|---|
| `logging/graph_logger.py` | `GraphLogger` 类，提供 graph 事件的记录方法 |
| `tests/test_deep_analysis_logging.py` | agent 日志的集成测试 |
| `tests/test_graph_logging.py` | graph 事件日志的单元测试 |

## 数据流

### LLM 日志 — 在 `DeepAnalyseAgent._process_iteration()` 中

```
# LLM 调用前
llm_logger.log_request_full(
    task_id, stage="deep_analysis", model,
    messages=[system prompt + 完整对话历史], tools=[...]
)

response = await self._llm.chat(messages, tools, ...)

# LLM 调用后
llm_logger.log_response_full(
    task_id, stage, model,
    content=response.content,
    tool_calls=response.tool_calls,
    input_tokens, output_tokens, latency_ms, finish_reason
)
```

### 工具日志 — 在 `_process_iteration()` 的工具调用循环中

```
t0 = time()
result = await execute_tool_with_validation(tc.name, tc.arguments)
elapsed = time() - t0

tool_logger.log_execution(
    task_id, stage, tool=tc.name,
    arguments=tc.arguments,   # 完整
    result=result,            # 完整
    duration_ms=elapsed
)
```

### Graph 日志 — 在 `graph.py` 核心函数中

| 触发点 | GraphLogger 调用 |
|---|---|
| `create_agent()` | `log_agent_created(child_id, parent_id, name, task)` |
| `send_message_to_agent()` | `log_message_sent(from, to, msg_id, type, priority)` |
| `wait_for_message()` | `log_agent_status_change(id, "running", "waiting", reason)` |
| `agent_finish()` | `log_agent_finished(id, success, findings_count, summary)` |
| `agent_loop()` 状态恢复 | `log_agent_status_change(id, "waiting", "running")` |

### Logger 注入路径

```
deep_analysis.py main()
  ├── LLMLogger(workspace / "logs" / "llm.jsonl")
  ├── ToolLogger(workspace / "logs" / "tools.jsonl")
  ├── GraphLogger(workspace / "logs" / "graph.jsonl")
  │
  ├── RootAgent(state, llm_provider, llm_logger, tool_logger)
  │     └── DeepAnalyseAgent 保存为 self._llm_logger / self._tool_logger
  │
  └── graph.set_graph_logger(graph_logger)  # 模块级变量注入
```

**关键细节**：`graph.py` 中的核心函数已注册为 LLM tool（签名由 LLM 决定），
不能直接加 `graph_logger` 参数。因此 `GraphLogger` 通过 `graph.py` 的模块级
变量 `_graph_logger` 注入，与 `_agent_graph`、`_agent_states` 等现有全局状态
的模式保持一致。`deep_analysis.py` 在启动时调用 `set_graph_logger()` 设置，
各核心函数内部读取 `_graph_logger` 并调用。

### 目录结构

```
{task_workspace}/
  logs/
    llm.jsonl
    tools.jsonl
    graph.jsonl
```

## GraphLogger API

```python
class GraphLogger:
    def __init__(self, path: Path) -> None: ...

    def log_agent_created(
        self, task_id: str, stage: str,
        agent_id: str, parent_id: str | None,
        name: str, task: str,
    ) -> None: ...

    def log_agent_status_change(
        self, task_id: str, stage: str,
        agent_id: str, old_status: str, new_status: str,
        reason: str = "",
    ) -> None: ...

    def log_message_sent(
        self, task_id: str, stage: str,
        from_id: str, to_id: str,
        msg_id: str, msg_type: str, priority: str,
    ) -> None: ...

    def log_agent_finished(
        self, task_id: str, stage: str,
        agent_id: str, success: bool,
        findings_count: int, result_summary: str,
    ) -> None: ...
```

所有方法通过 `JSONLLogger` 写入，category 为 `"graph"`，level 为 `"info"`。

## 错误处理

- `JSONLLogger.write()` 内部 catch 所有异常，失败时降级到
  `logging.warning()` 输出告警，不向上抛出
- JSON 不可序列化的数据最终通过 `repr()` 兜底
- `GraphLogger` 方法遇到未知 `agent_id` 时记录 warning，不抛异常

## 测试

### 单元测试 — 扩展 `tests/test_logging.py`

- `test_llm_logger_full_request` — 完整 messages 数组被记录
- `test_llm_logger_full_response` — 完整 content 和 tool_calls 被记录
- `test_tool_logger_full_execution` — 完整 arguments 和 result 被记录
- `test_graph_logger_agent_created` — 事件格式校验
- `test_graph_logger_status_change` — 事件格式校验
- `test_graph_logger_message_sent` — 事件格式校验
- `test_graph_logger_agent_finished` — 事件格式校验

### 集成测试 — 新建 `tests/test_deep_analysis_logging.py`

- 用 mock LLM provider + 真实 loggers 运行 `DeepAnalyseAgent`
  一次迭代，断言 `llm.jsonl` 和 `tools.jsonl` 产出
- 验证 logger 写入抛异常时 agent 不崩溃

### Graph 日志测试 — 新建 `tests/test_graph_logging.py`

- 调用 `create_agent`、`send_message_to_agent`、`agent_finish`，
  传入 `GraphLogger`，断言 `graph.jsonl` 事件产出
