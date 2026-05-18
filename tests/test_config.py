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
