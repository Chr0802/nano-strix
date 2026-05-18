import pytest

from nano_strix.sandbox.base import ExecutionResult, Sandbox, SandboxConfig


def test_sandbox_config_defaults():
    cfg = SandboxConfig()
    assert cfg.sandbox_type == "process"
    assert cfg.timeout == 600


def test_sandbox_is_abstract():
    with pytest.raises(TypeError):
        Sandbox()


def test_execution_result():
    r = ExecutionResult(exit_code=0, stdout="ok", stderr="", duration=0.5)
    assert r.success is True


def test_execution_result_failure():
    r = ExecutionResult(exit_code=1, stdout="", stderr="error", duration=1.0)
    assert r.success is False
