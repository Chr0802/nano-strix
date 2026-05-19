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
    SchedulerConfig,
    StageConcurrency,
)


def _load_scheduler(data: dict) -> SchedulerConfig:
    if not data:
        return SchedulerConfig()
    stages_raw = data.get("stages", {})
    stages = {}
    for name, values in stages_raw.items():
        if isinstance(values, dict):
            stages[name] = StageConcurrency(**values)
        else:
            stages[name] = values
    return SchedulerConfig(stages=stages)


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
    scheduler_data = data.get("scheduler", {})

    return AppConfig(
        llm=LLMConfig(**llm_data) if llm_data else LLMConfig(),
        pipeline=PipelineConfig(**pipeline_data) if pipeline_data else PipelineConfig(),
        sandbox=SandboxConfig(**sandbox_data) if sandbox_data else SandboxConfig(),
        ipc=IPCConfig(**ipc_data) if ipc_data else IPCConfig(),
        logging=LoggingConfig(**logging_data) if logging_data else LoggingConfig(),
        scheduler=_load_scheduler(scheduler_data),
    )
