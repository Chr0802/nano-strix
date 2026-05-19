# CLI 补全设计

**日期:** 2026-05-19
**状态:** 已确认

## 概述

补全 CLI 层，让 `run`、`run-batch`、`resume` 命令真正驱动 pipeline 执行。移除 `report` CLI 命令——报告生成改为 pipeline 的一个 stage agent 负责。

## 改动

### 1. `_execute_pipeline()` 辅助函数 (cli.py 新增)

`run` 和 `run-batch` 共用的内部异步函数：

```
async def _execute_pipeline(
    workspace, config, targets, stages, input_overrides, verbose
) -> list[str]
```

职责：
- 创建 EventBus(workspace / "tasks")、AgentManager、StageScheduler
- 将 stages 和 input_overrides 注入 config.pipeline
- 打印 pipeline 信息（目标数量、stage 链路、各 stage 并发/重试配置）
- `await scheduler.submit_batch(targets)` → `await scheduler.run()`
- 逐任务打印最终状态（completed / failed）
- 返回 task_ids 列表，提示用户可用 `resume` 恢复失败任务

### 2. `run` 命令

保留现有参数。`--target` 改为可选。新增 `--targets-file`：

| 参数 | 说明 |
|---|---|
| `--target` | 单个目标目录（与 `--targets-file` 二选一或同时提供） |
| `--targets-file` | 文件路径，每行一个目标，忽略空行和 `#` 注释 |
| `--pipeline` | preset 名或逗号分隔的 stage 列表（默认 `full`） |
| `--input` | 输入覆盖（可多次使用，格式 `key=path`） |
| `--config` | 配置文件路径 |
| `--model` | 覆盖模型 |
| `--output` | 输出目录 |
| `--verbose` | 详细日志 |
| `--no-snapshot` | 原地分析，不复制目标 |

逻辑：
1. 加载配置，`--model` 覆盖模型
2. 解析 `--pipeline`：先匹配 preset（full/analysis/exploit/quick），否则按逗号拆分
3. 收集 targets：从 `--target` 和/或 `--targets-file` 读取
4. 校验至少有一个 target
5. 解析 `--input` 为 `{key: path}` 字典
6. `asyncio.run(_execute_pipeline(...))`
7. 完成后打印提示：`"结果已保存。使用 'nano-strix resume <id>' 恢复失败任务。"`

### 3. `run-batch` 命令

委托给 `_execute_pipeline()`。保留作为薄封装，向后兼容。

### 4. `resume` 命令

```
resume TASK_ID [--config PATH] [--output DIR]
```

逻辑：
1. 从 `workspace/tasks/{task_id}/state.json` 加载 TaskState
2. 文件不存在 → 报错 "任务未找到"
3. 所有 stage 都已完成 → 打印 "任务已完成"，退出
4. 找到第一个不在 `stage_results` 中的 stage
5. 从 events.jsonl 中的 `task_created` 事件提取 target 路径
6. `scheduler.resume_task(task_id, target_path)` — 将任务放入第一个未完成 stage 的队列
7. `await scheduler.run()`
8. 打印最终状态

`resume --all` 留待后续扩展。

### 5. `report` 命令

从 CLI 移除。报告生成由 `agents/report.py` 作为 pipeline stage 负责。

### 6. `agents/report.py` (新增)

与其他三个 agent 保持一致的 stub 模式：

```python
"""strix-report agent: 报告生成。尚未实现。"""
import json, sys

def main():
    line = sys.stdin.readline()
    msg = json.loads(line)
    result = {
        "type": "result",
        "task_id": msg["task_id"],
        "payload": {"error": "report agent 尚未实现"},
    }
    print(json.dumps(result))

if __name__ == "__main__":
    main()
```

### 7. `orchestrator/runner.py` — STAGE_SCRIPTS 更新

加入：`"report": "src/nano_strix/agents/report.py"`

### 8. `orchestrator/scheduler.py` — 新增 `resume_task()` 方法

```python
async def resume_task(self, task_id: str, target_path: str) -> None:
    state = self._event_bus.get_state(task_id)
    for stage in self._stages:
        if stage not in state.stage_results:
            self._remaining += 1
            await self._queues[stage].put((task_id, target_path))
            return
```

`_run_stage_worker` 无需改动——它已经支持任务从任意 stage 开始并按序流转到后续 stage。

## 涉及文件

| 文件 | 改动 |
|---|---|
| `src/nano_strix/cli.py` | 新增 `_execute_pipeline()`，重写 `run`，`run-batch` 委托，重写 `resume`，移除 `report` |
| `src/nano_strix/orchestrator/scheduler.py` | 新增 `resume_task()` 方法 |
| `src/nano_strix/orchestrator/runner.py` | STAGE_SCRIPTS 增加 `report` |
| `src/nano_strix/agents/report.py` | 新建 stub agent |

## 边界情况

- **无 targets:** `run` 既没 `--target` 也没 `--targets-file` → 报错退出
- **任务已完成时 resume:** 打印状态，正常退出
- **任务不存在时 resume:** 报错，非零退出
- **status 为 running 时 resume:** 视为中断进程残留，按相同逻辑从断点恢复
- **stage_results 为空:** 全部 stage 从 per_file 重新执行
