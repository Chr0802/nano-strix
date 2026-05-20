import tempfile
from pathlib import Path

from nano_strix.config.loader import load_config
from nano_strix.config.schema import (
    AppConfig,
    LoggingConfig,
    PipelineConfig,
    SandboxConfig,
)


def test_default_config():
    cfg = AppConfig()
    assert cfg.llm.provider == "anthropic"
    assert cfg.pipeline.stages == ["per_file", "cross_file", "exploit", "report"]
    assert cfg.ipc.timeout_seconds == 300


def test_sandbox_config():
    cfg = SandboxConfig(sandbox_type="docker", image="python:3.12-slim")
    assert cfg.sandbox_type == "docker"
    assert cfg.network == "none"
    assert cfg.memory_limit == "512m"


def test_pipeline_presets():
    cfg = PipelineConfig()
    assert cfg.stages == ["per_file", "cross_file", "exploit", "report"]


def test_load_config_from_yaml():
    yaml_content = """
llm:
  provider: openai
  api_key: sk-test
  model: gpt-4o
pipeline:
  stages:
    - per_file
    - report
ipc:
  timeout_seconds: 600
"""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write(yaml_content)
        f.flush()
        cfg = load_config(Path(f.name))

    assert cfg.llm.provider == "openai"
    assert cfg.llm.api_key == "sk-test"
    assert cfg.pipeline.stages == ["per_file", "report"]
    assert cfg.ipc.timeout_seconds == 600


def test_load_config_missing_file():
    cfg = load_config(Path("/nonexistent/config.yaml"))
    assert cfg.llm.provider == "anthropic"  # falls back to defaults


def test_logging_config():
    cfg = LoggingConfig(level="debug")
    assert cfg.level == "debug"
    assert cfg.categories == {}


def test_per_file_config_defaults():
    from nano_strix.config.schema import PerFileConfig, PerFileAgentConfig

    cfg = PerFileConfig()
    assert cfg.classification_model == "claude-haiku-4-5-20251001"
    assert cfg.analysis_model == "claude-sonnet-4-6"
    assert cfg.max_concurrent == 4
    assert cfg.max_file_retries == 3
    assert cfg.orphan_timeout_seconds == 600
    assert cfg.max_agent_restarts == 3
    assert len(cfg.agents) == 4
    assert cfg.agents["route_agent"].enabled is True
    assert cfg.agents["route_agent"].max_iterations == 300


def test_per_file_config_nested_in_app_config():
    from nano_strix.config.schema import AppConfig

    cfg = AppConfig()
    assert cfg.per_file is not None
    assert cfg.per_file.classification_model == "claude-haiku-4-5-20251001"
