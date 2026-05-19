from pathlib import Path

import click

from nano_strix.config.loader import load_config
from nano_strix.config.paths import DEFAULT_CONFIG_PATH
from nano_strix.config.schema import AppConfig


@click.group()
@click.version_option(package_name="nano-strix")
def main():
    """nano-strix -- LLM-driven penetration testing agent."""


@main.command()
def hello():
    """Say hello."""
    click.echo("Hello from nano-strix!")


@main.command()
def version():
    """Show version info."""
    click.echo("nano-strix 0.1.0")


@main.group()
def config():
    """Configuration management."""


@config.command("init")
def config_init():
    """Generate default config file."""
    import yaml

    DEFAULT_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    cfg = AppConfig()
    with open(DEFAULT_CONFIG_PATH, "w") as f:
        yaml.dump(
            {
                "llm": {"provider": cfg.llm.provider, "model": cfg.llm.model},
                "pipeline": {"stages": cfg.pipeline.stages},
                "ipc": {"timeout_seconds": cfg.ipc.timeout_seconds},
                "logging": {"level": cfg.logging.level},
            },
            f,
            default_flow_style=False,
        )
    click.echo(f"Config written to {DEFAULT_CONFIG_PATH}")


@config.command("show")
def config_show():
    """Show current config."""
    import yaml

    cfg = load_config(DEFAULT_CONFIG_PATH)
    data = {
        "llm": {"provider": cfg.llm.provider, "model": cfg.llm.model},
        "pipeline": {"stages": cfg.pipeline.stages},
        "ipc": {"timeout_seconds": cfg.ipc.timeout_seconds},
        "logging": {"level": cfg.logging.level},
    }
    click.echo(yaml.dump(data, default_flow_style=False))


@main.command()
@click.option(
    "--target",
    required=True,
    type=click.Path(exists=True),
    help="Target code directory",
)
@click.option(
    "--pipeline",
    default="full",
    help="Pipeline preset or comma-separated stages",
)
@click.option(
    "--input",
    "input_overrides",
    multiple=True,
    help="Input overrides (key=path)",
)
@click.option("--config", "config_path", type=click.Path(), help="Config file path")
@click.option("--model", help="Override default model")
@click.option("--output", type=click.Path(), help="Output directory")
@click.option("--verbose", is_flag=True, help="Verbose logging")
@click.option("--no-snapshot", is_flag=True, help="Analyze target in-place (no copy)")
def run(
    target, pipeline, input_overrides, config_path, model, output, verbose, no_snapshot
):
    """Run a penetration test pipeline."""
    load_config(Path(config_path) if config_path else DEFAULT_CONFIG_PATH)  # noqa: F841

    pipeline_presets = {
        "full": ["per_file", "cross_file", "exploit", "report"],
        "analysis": ["per_file", "cross_file", "report"],
        "exploit": ["exploit", "report"],
        "quick": ["per_file", "report"],
    }

    if pipeline in pipeline_presets:
        stages = pipeline_presets[pipeline]
    else:
        stages = [s.strip() for s in pipeline.split(",")]

    overrides = {}
    for item in input_overrides:
        key, _, path = item.partition("=")
        overrides[key] = path

    click.echo(f"Target: {target}")
    click.echo(f"Pipeline: {' -> '.join(stages)}")
    if overrides:
        click.echo(f"Input overrides: {overrides}")
    click.echo("Starting pipeline...")


@main.command()
@click.argument("task_id")
def resume(task_id):
    """Resume an interrupted task."""
    click.echo(f"Resuming task {task_id}...")


@main.command()
@click.argument("task_id")
@click.option(
    "--format",
    "fmt",
    default="markdown",
    type=click.Choice(["markdown", "html", "pdf"]),
)
def report(task_id, fmt):
    """Regenerate report from existing results."""
    click.echo(f"Generating {fmt} report for task {task_id}...")


@main.command("run-batch")
@click.argument("targets_file", type=click.Path(exists=True))
@click.option("--config", "config_path", type=click.Path(), help="Config file path")
@click.option("--model", help="Override default model")
@click.option("--output", type=click.Path(), help="Output directory")
@click.option("--verbose", is_flag=True, help="Verbose logging")
def run_batch(targets_file, config_path, model, output, verbose):
    """Run pipeline on multiple targets from a file (one path per line)."""
    import asyncio

    from nano_strix.agents.manager import AgentManager
    from nano_strix.bus.queue import EventBus
    from nano_strix.orchestrator.scheduler import StageScheduler

    cfg = load_config(Path(config_path) if config_path else DEFAULT_CONFIG_PATH)
    if model:
        cfg.llm.model = model

    targets_path = Path(targets_file)
    targets = [
        line.strip()
        for line in targets_path.read_text().splitlines()
        if line.strip() and not line.startswith("#")
    ]

    if not targets:
        click.echo("No targets found in file.")
        return

    workspace = Path(output) if output else Path.cwd()
    workspace.mkdir(parents=True, exist_ok=True)

    event_bus = EventBus(workspace / "tasks")
    agent_manager = AgentManager(workspace=workspace, config=cfg.ipc)
    scheduler = StageScheduler(
        workspace=workspace,
        config=cfg,
        agent_manager=agent_manager,
        event_bus=event_bus,
    )

    click.echo(f"Targets: {len(targets)}")
    click.echo(f"Pipeline: {' -> '.join(cfg.pipeline.stages)}")
    for stage, sc in cfg.scheduler.stages.items():
        click.echo(
            f"  {stage}: max_concurrent={sc.max_concurrent}, "
            f"max_retries={sc.max_retries}"
        )
    click.echo("Starting batch...")

    async def _run():
        task_ids = await scheduler.submit_batch(targets)
        click.echo(f"Submitted {len(task_ids)} tasks")
        await scheduler.run()
        for tid in task_ids:
            state = event_bus.get_state(tid)
            click.echo(f"  {tid}: {state.status}")

    asyncio.run(_run())
