from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class StageConcurrency:
    max_concurrent: int = 1
    max_retries: int = 2


@dataclass
class SchedulerConfig:
    stages: dict[str, StageConcurrency] = field(
        default_factory=lambda: {
            "deep_analysis": StageConcurrency(max_concurrent=2, max_retries=2),
            "exploit": StageConcurrency(max_concurrent=1, max_retries=2),
            "report": StageConcurrency(max_concurrent=1, max_retries=0),
        }
    )


@dataclass
class PerFileAgentConfig:
    enabled: bool = True
    max_iterations: int = 300


@dataclass
class PerFileConfig:
    agents: dict[str, PerFileAgentConfig] = field(
        default_factory=lambda: {
            "route_agent": PerFileAgentConfig(),
            "dataflow_agent": PerFileAgentConfig(),
            "auth_agent": PerFileAgentConfig(),
            "dependency_agent": PerFileAgentConfig(),
        }
    )
    classification_model: str = "claude-haiku-4-5-20251001"
    analysis_model: str = "claude-sonnet-4-6"
    max_concurrent: int = 4
    max_tokens: int = 4096
    temperature: float = 0.1
    phase3_timeout_seconds: int = 1800
    per_file_timeout_seconds: int = 3600
    max_file_retries: int = 3
    orphan_timeout_seconds: int = 600
    max_agent_restarts: int = 3
    manifest_sync_interval_seconds: int = 5
    health_check_interval_seconds: int = 30
    static_scanners: list[str] = field(default_factory=lambda: ["semgrep", "bandit"])


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
        default_factory=lambda: ["deep_analysis", "exploit", "report"]
    )
    input_overrides: dict[str, str] = field(default_factory=dict)


@dataclass
class SandboxConfig:
    sandbox_type: str = "docker"  # docker | process
    image: str = "nano-strix-sandbox:latest"
    network: str = "none"
    tool_server_port: int = 8080
    memory_limit: str = "512m"
    cpu_limit: float = 1.0
    timeout: int = 600
    env_vars: dict[str, str] = field(default_factory=dict)
    volumes: list[dict] = field(default_factory=list)
    resources: dict[str, Any] = field(default_factory=dict)


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
class DeepAnalysisConfig:
    """Configuration for the deep-analysis stage."""
    classification_model: str = "claude-haiku-4-5-20251001"
    analysis_model: str = "claude-sonnet-4-6"
    max_concurrent_llm: int = 4
    max_tokens: int = 4096
    temperature: float = 0.1
    phase1_split_threshold: int = 50
    phase2_split_threshold: int = 100
    phase3_split_threshold: int = 50
    phase4_split_threshold: int = 30
    phase5_split_threshold: int = 50
    per_file_timeout_seconds: int = 3600
    max_agent_iterations: int = 300
    agent_waiting_timeout: int = 600


@dataclass
class SkillsConfig:
    """Configuration for the skills loading system."""
    skills_dir: str = ""


@dataclass
class AppConfig:
    llm: LLMConfig = field(default_factory=LLMConfig)
    pipeline: PipelineConfig = field(default_factory=PipelineConfig)
    sandbox: SandboxConfig = field(default_factory=SandboxConfig)
    ipc: IPCConfig = field(default_factory=IPCConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    scheduler: SchedulerConfig = field(default_factory=SchedulerConfig)
    per_file: PerFileConfig = field(default_factory=PerFileConfig)
    deep_analysis: DeepAnalysisConfig = field(default_factory=DeepAnalysisConfig)
    skills: SkillsConfig = field(default_factory=SkillsConfig)
