from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from nano_strix.config.schema import AppConfig, PipelineConfig

STAGE_SCRIPTS = {
    "per_file": "src/nano_strix/agents/per_file.py",
    "cross_file": "src/nano_strix/agents/cross_file.py",
    "exploit": "src/nano_strix/agents/exploit.py",
}


class OrchestratorRunner:
    def __init__(self, workspace: Path, config: AppConfig) -> None:
        self._workspace = workspace
        self._config = config

    def get_stages(self, pipeline: PipelineConfig) -> list[str]:
        return pipeline.stages

    def resolve_input(self, key: str, path: str) -> dict[str, Any] | None:
        p = Path(path)
        if not p.exists():
            return None
        with open(p) as f:
            return json.load(f)

    def get_stage_script(self, stage: str) -> str | None:
        return STAGE_SCRIPTS.get(stage)
