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
    config_file = tmp_path / "config.yaml"
    monkeypatch.setattr(
        "nano_strix.cli.DEFAULT_CONFIG_PATH", config_file,
    )
    runner = CliRunner()
    result = runner.invoke(main, ["config", "init"])
    assert result.exit_code == 0
    assert config_file.exists()


def test_cli_config_show(tmp_path, monkeypatch):
    config_file = tmp_path / "config.yaml"
    monkeypatch.setattr(
        "nano_strix.cli.DEFAULT_CONFIG_PATH", config_file,
    )
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


def test_cli_run_with_target(tmp_path):
    target_dir = tmp_path / "target"
    target_dir.mkdir()
    (target_dir / "app.py").write_text("print('hello')")

    workspace = tmp_path / "output"

    runner = CliRunner()
    result = runner.invoke(main, [
        "run",
        "--target", str(target_dir),
        "--output", str(workspace),
        "--pipeline", "quick",
    ])
    assert result.exit_code == 0
    assert "Submitted" in result.output
    assert "deep_analysis" in result.output


def test_cli_run_missing_target():
    runner = CliRunner()
    result = runner.invoke(main, ["run", "--pipeline", "quick"])
    assert result.exit_code != 0
    assert "target" in result.output.lower()


def test_cli_run_batch_with_file(tmp_path):
    target1 = tmp_path / "t1"
    target1.mkdir()
    target2 = tmp_path / "t2"
    target2.mkdir()

    targets_file = tmp_path / "targets.txt"
    targets_file.write_text(f"{target1}\n{target2}\n")

    workspace = tmp_path / "output"

    runner = CliRunner()
    result = runner.invoke(main, [
        "run-batch",
        str(targets_file),
        "--output", str(workspace),
    ])
    assert result.exit_code == 0
    assert "Submitted 2" in result.output


def test_cli_resume_nonexistent_task(tmp_path):
    workspace = tmp_path / "output"
    workspace.mkdir()

    runner = CliRunner()
    result = runner.invoke(main, [
        "resume",
        "t-nonexistent",
        "--output", str(workspace),
    ])
    assert result.exit_code != 0
    assert "not found" in result.output.lower()
