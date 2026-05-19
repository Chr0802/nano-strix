# 调度器系统 (StageScheduler)

## 概述

调度器系统支持批量任务提交和按阶段并发控制。多个任务之间并行执行，单个任务内各阶段串行执行。

```
Task A: per_file ──→ cross_file ──→ exploit ──→ report
Task B: per_file ──→ cross_file ──→ exploit ──→ report
Task C: per_file ──→ cross_file ──→ exploit ──→ report

阶段并发:  per_file=2    cross_file=1   exploit=1   report=1
           ┌─────────┐
           │ A, B 运行 │  C 等待空闲槽位
           └─────────┘
```

## 核心组件

### StageScheduler (`orchestrator/scheduler.py`)

调度器核心类，负责：
- 管理每个阶段的 `asyncio.Semaphore` 并发限制
- 通过 `asyncio.Queue` 实现阶段间任务传递
- 按阶段重试机制，失败任务重试 N 次后标记为失败

#### 主要方法

```python
async def submit_task(target_path: str) -> str
# 提交单个任务，返回 task_id

async def submit_batch(targets: list[str]) -> list[str]
# 批量提交任务，返回 task_id 列表

async def run() -> None
# 启动调度器，创建每个阶段的 worker 并等待完成
```

#### 内部机制

- `_remaining`: 原子计数器，跟踪剩余未完成的任务数
- `_mark_done()`: 任务完成时递减计数器，当计数器归零时向前一阶段发送 sentinel
- `_run_stage_worker()`: 每个阶段的 worker 循环，从队列取任务、获取信号量、执行、释放
- `_execute_stage()`: 执行单个任务的单个阶段，包含重试逻辑

### SchedulerConfig (`config/schema.py`)

```python
@dataclass
class StageConcurrency:
    max_concurrent: int = 1    # 该阶段最大并发数
    max_retries: int = 2       # 该阶段最大重试次数

@dataclass
class SchedulerConfig:
    stages: dict[str, StageConcurrency]  # 每阶段的并发和重试配置
```

### EventBus 扩展 (`bus/queue.py`)

新增方法：
- `get_tasks_by_status(status: str) -> list[TaskState]` — 按状态查询任务

TaskState 新增字段：
- `retry_counts: dict[str, int]` — 每阶段的重试次数记录

## 配置示例

```yaml
scheduler:
  stages:
    per_file:
      max_concurrent: 2
      max_retries: 2
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

## CLI 使用

```bash
# 创建目标文件
echo "/path/to/target1" > targets.txt
echo "/path/to/target2" >> targets.txt
echo "/path/to/target3" >> targets.txt

# 批量运行
nano-strix run-batch targets.txt

# 指定配置文件
nano-strix run-batch targets.txt --config /path/to/config.yaml
```

## 任务生命周期

```
submit_task → EventBus.create_task → 入队到第一阶段队列
stage_worker 取出 → semaphore.acquire → 调度 agent
  → 成功: complete_stage, 入队到下一阶段队列
  → 失败: 重试 (最多 max_retries 次), 然后标记失败
  → semaphore.release
```

## 事件类型

| event_type | 说明 |
|-----------|------|
| `task_created` | 任务创建 |
| `stage_started` | 阶段开始执行 |
| `stage_completed` | 阶段执行成功 |
| `task_completed` | 所有阶段完成 |
| `task_failed` | 任务失败（重试耗尽） |

## 文件清单

| 文件 | 说明 |
|------|------|
| `src/nano_strix/orchestrator/scheduler.py` | StageScheduler 核心实现 |
| `src/nano_strix/config/schema.py` | StageConcurrency, SchedulerConfig |
| `src/nano_strix/config/loader.py` | scheduler 配置加载 |
| `src/nano_strix/bus/events.py` | TaskState.retry_counts |
| `src/nano_strix/bus/queue.py` | get_tasks_by_status() |
| `src/nano_strix/cli.py` | run-batch 命令 |
| `tests/test_scheduler.py` | 调度器测试 (6 个) |
