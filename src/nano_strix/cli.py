import asyncio
import logging
from pathlib import Path

import click

from nano_strix.agents.manager import AgentManager
from nano_strix.bus.queue import EventBus
from nano_strix.config.loader import load_config
from nano_strix.config.paths import DEFAULT_CONFIG_PATH
from nano_strix.config.schema import AppConfig
from nano_strix.orchestrator.scheduler import StageScheduler

logger = logging.getLogger(__name__)


async def _execute_pipeline(
    workspace: Path,
    config: AppConfig,
    targets: list[str],
    stages: list[str],
    input_overrides: dict[str, str] | None = None,
    verbose: bool = False,
) -> list[str]:
    config.pipeline.stages = stages
    if input_overrides:
        config.pipeline.input_overrides = input_overrides

    tasks_dir = workspace / "tasks"
    event_bus = EventBus(tasks_dir)
    agent_manager = AgentManager(workspace=workspace, config=config.ipc)
    scheduler = StageScheduler(
        workspace=workspace,
        config=config,
        agent_manager=agent_manager,
        event_bus=event_bus,
    )

    click.echo(f"Targets: {len(targets)}")
    click.echo(f"Pipeline: {' -> '.join(stages)}")
    if verbose:
        for stage_name, sc in config.scheduler.stages.items():
            click.echo(
                f"  {stage_name}: max_concurrent={sc.max_concurrent}, "
                f"max_retries={sc.max_retries}"
            )

    task_ids = await scheduler.submit_batch(targets)
    click.echo(f"Submitted {len(task_ids)} tasks")
    await scheduler.run()

    failed_count = 0
    for tid in task_ids:
        state = event_bus.get_state(tid)
        status_label = state.status.upper()
        if state.status == "failed":
            failed_count += 1
            click.echo(f"  [{status_label}] {tid}: {state.error or 'unknown error'}")
        else:
            click.echo(f"  [{status_label}] {tid}")

    if failed_count > 0:
        click.echo(
            f"\n{failed_count} task(s) failed. "
            f"Use 'nano-strix resume <task_id>' to retry."
        )

    return task_ids


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
    required=False,
    type=click.Path(exists=True),
    help="Target code directory",
)
@click.option(
    "--targets-file",
    type=click.Path(exists=True),
    help="File with one target path per line",
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
    target, targets_file, pipeline, input_overrides, config_path, model, output, verbose, no_snapshot
):
    """Run a penetration test pipeline."""
    if not target and not targets_file:
        raise click.UsageError("Either --target or --targets-file must be provided.")

    cfg = load_config(Path(config_path) if config_path else DEFAULT_CONFIG_PATH)
    if model:
        cfg.llm.model = model

    pipeline_presets = {
        "full": ["per_file", "cross_file", "exploit", "report"],
        "analysis": ["per_file", "cross_file", "report"],
        "exploit_only": ["exploit", "report"],
        "quick": ["per_file", "report"],
    }

    if pipeline in pipeline_presets:
        stages = pipeline_presets[pipeline]
    else:
        stages = [s.strip() for s in pipeline.split(",")]

    targets = []
    if target:
        targets.append(target)
    if targets_file:
        targets_path = Path(targets_file)
        for line in targets_path.read_text().splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#"):
                targets.append(stripped)

    if not targets:
        raise click.UsageError("No targets found.")

    overrides = {}
    for item in input_overrides:
        key, _, path = item.partition("=")
        overrides[key] = path

    workspace = Path(output) if output else Path.cwd()
    workspace.mkdir(parents=True, exist_ok=True)

    if verbose:
        logging.basicConfig(level=logging.DEBUG)

    asyncio.run(_execute_pipeline(
        workspace=workspace,
        config=cfg,
        targets=targets,
        stages=stages,
        input_overrides=overrides,
        verbose=verbose,
    ))


@main.command()
@click.argument("task_id")
@click.option("--config", "config_path", type=click.Path(), help="Config file path")
@click.option("--output", type=click.Path(), help="Output directory (workspace)")
def resume(task_id, config_path, output):
    """Resume an interrupted task."""
    cfg = load_config(Path(config_path) if config_path else DEFAULT_CONFIG_PATH)

    workspace = Path(output) if output else Path.cwd()
    tasks_dir = workspace / "tasks"

    if not (tasks_dir / task_id / "state.json").exists():
        raise click.ClickException(f"Task not found: {task_id}")

    event_bus = EventBus(tasks_dir)
    state = event_bus.get_state(task_id)

    # Check if already fully completed
    incomplete_stages = [
        s for s in state.stages if s not in state.stage_results
    ]
    if not incomplete_stages:
        click.echo(f"Task {task_id} already completed.")
        return

    # Extract target path from task_created event
    events = event_bus.get_events(task_id)
    target_path = None
    for ev in events:
        if ev.event_type == "task_created":
            target_path = ev.payload.get("target")
            break

    if not target_path:
        target_path = "unknown"

    click.echo(
        f"Resuming task {task_id}: "
        f"remaining stages: {' -> '.join(incomplete_stages)}"
    )

    agent_manager = AgentManager(workspace=workspace, config=cfg.ipc)
    scheduler = StageScheduler(
        workspace=workspace,
        config=cfg,
        agent_manager=agent_manager,
        event_bus=event_bus,
    )

    async def _resume():
        await scheduler.resume_task(task_id, target_path)
        await scheduler.run()
        final_state = event_bus.get_state(task_id)
        status_label = final_state.status.upper()
        if final_state.status == "failed":
            click.echo(f"  [{status_label}] {task_id}: {final_state.error or 'unknown error'}")
        else:
            click.echo(f"  [{status_label}] {task_id}")

    asyncio.run(_resume())


@main.command("run-batch")
@click.argument("targets_file", type=click.Path(exists=True))
@click.option("--config", "config_path", type=click.Path(), help="Config file path")
@click.option("--model", help="Override default model")
@click.option("--output", type=click.Path(), help="Output directory")
@click.option("--verbose", is_flag=True, help="Verbose logging")
def run_batch(targets_file, config_path, model, output, verbose):
    """Run pipeline on multiple targets from a file (one path per line)."""
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
        raise click.UsageError("No targets found in file.")

    workspace = Path(output) if output else Path.cwd()
    workspace.mkdir(parents=True, exist_ok=True)

    if verbose:
        logging.basicConfig(level=logging.DEBUG)

    asyncio.run(_execute_pipeline(
        workspace=workspace,
        config=cfg,
        targets=targets,
        stages=cfg.pipeline.stages,
        verbose=verbose,
    ))
