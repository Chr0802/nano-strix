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
