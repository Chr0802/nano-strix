from click.testing import CliRunner

from nano_strix.cli import main


def test_hello():
    runner = CliRunner()
    result = runner.invoke(main, ["hello"])
    assert result.exit_code == 0
    assert "Hello from nano-strix!" in result.output


def test_version():
    runner = CliRunner()
    result = runner.invoke(main, ["--version"])
    assert result.exit_code == 0
    assert "0.1.0" in result.output


def test_cli_version():
    runner = CliRunner()
    result = runner.invoke(main, ["version"])
    assert result.exit_code == 0
    assert "nano-strix" in result.output


def test_cli_config_init(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    result = runner.invoke(main, ["config", "init"])
    assert result.exit_code == 0


def test_cli_config_show():
    runner = CliRunner()
    result = runner.invoke(main, ["config", "show"])
    assert result.exit_code == 0


def test_cli_run_help():
    runner = CliRunner()
    result = runner.invoke(main, ["run", "--help"])
    assert result.exit_code == 0
    assert "--target" in result.output
    assert "--pipeline" in result.output


def test_cli_run_batch_help():
    runner = CliRunner()
    result = runner.invoke(main, ["run-batch", "--help"])
    assert result.exit_code == 0
    assert "TARGETS_FILE" in result.output
