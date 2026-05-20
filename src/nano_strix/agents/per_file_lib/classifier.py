# src/nano_strix/agents/per_file_lib/classifier.py
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from nano_strix.agents.per_file_lib.manifest import FileManifest

logger = logging.getLogger(__name__)

VALID_PRIORITIES = frozenset({"high", "medium", "low"})


def _validate_classified_entry(entry: Any) -> dict[str, Any]:
    """Validate a classified entry has the expected shape.

    Converts invalid entries to fallback defaults
    ``{"priority": "medium", "dimensions": []}``.
    """
    if not isinstance(entry, dict):
        return {"priority": "medium", "dimensions": []}

    priority = entry.get("priority")
    if not isinstance(priority, str) or priority not in VALID_PRIORITIES:
        priority = "medium"

    dimensions = entry.get("dimensions")
    if not isinstance(dimensions, list):
        dimensions = []
    else:
        # Filter out non-string dimension entries
        valid_dims = [d for d in dimensions if isinstance(d, str)]
        dimensions = valid_dims

    return {"priority": priority, "dimensions": dimensions}

CLASSIFIER_SYSTEM_PROMPT = """You are a code security analyst. Your task is to classify source code files by risk priority and analysis dimension.

For each file in the provided directory listing, assign:
1. **priority**: "high" | "medium" | "low"
   - high: auth, login, database queries, API routes, input handling, command execution
   - medium: business logic, middleware, model definitions, data transformation
   - low: config, utilities, static assets, tests, fixtures, type stubs
2. **dimensions**: list from ["route", "dataflow", "auth", "dependency"] (can be empty)
   - route: defines HTTP routes or API endpoints
   - dataflow: handles user input, database operations, command execution, file I/O
   - auth: authentication, authorization, session management, JWT, password hashing
   - dependency: imports third-party libraries, dependency declaration files

Return ONLY a JSON object with a "files" key mapping each file path to {"priority": ..., "dimensions": [...]}.
Do NOT include any other text."""


async def classify_files(
    target_dir: str,
    manifest_path: Path,
    llm_client,
    agent_names: list[str],
    max_file_retries: int = 3,
) -> FileManifest:
    """Phase 1: Discover files in target_dir and classify via LLM."""
    target = Path(target_dir)
    if not target.exists():
        raise FileNotFoundError(f"Target directory not found: {target_dir}")

    # Collect all files recursively
    all_files: list[str] = []
    for p in sorted(target.rglob("*")):
        if p.is_file() and not p.name.startswith("."):
            rel = str(p.relative_to(target))
            all_files.append(rel)

    if not all_files:
        return FileManifest.create(
            path=manifest_path,
            files={},
            agent_names=agent_names,
            max_file_retries=max_file_retries,
        )

    logger.info("Phase 1: discovered %d files in %s", len(all_files), target_dir)

    # Build prompt
    file_list = "\n".join(f"  - {f}" for f in all_files)
    user_prompt = f"Directory: {target_dir}\nFiles ({len(all_files)}):\n{file_list}"

    response = await llm_client.chat(
        messages=[
            {"role": "system", "content": CLASSIFIER_SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.1,
        max_tokens=8192,
    )

    # Parse LLM response
    raw = (response.content or "").strip()
    # Strip markdown code fences if present
    if raw.startswith("```"):
        lines = raw.split("\n")
        raw = "\n".join(lines[1:]) if lines else raw
    if raw.endswith("```"):
        raw = raw[:-3].strip()

    try:
        data = json.loads(raw)
        classified_raw = data.get("files", {})
        # Validate each entry has the expected shape; convert invalid entries to fallback
        classified = {
            f: _validate_classified_entry(entry) for f, entry in classified_raw.items()
        }
    except json.JSONDecodeError:
        logger.error("Failed to parse LLM classifier response: %s", raw[:500])
        # Fallback: all medium, no dimensions
        classified = {f: {"priority": "medium", "dimensions": []} for f in all_files}

    # Ensure all files are present in classified
    for f in all_files:
        if f not in classified:
            classified[f] = {"priority": "medium", "dimensions": []}

    # Build manifest dict from the known file list (classifier may return extra keys)
    files_dict = {f: classified[f] for f in all_files}

    manifest = FileManifest.create(
        path=manifest_path,
        files=files_dict,
        agent_names=agent_names,
        max_file_retries=max_file_retries,
    )
    manifest.phase = "classification"
    manifest.save()

    logger.info("Phase 1 complete: %d files classified", len(files_dict))
    return manifest
