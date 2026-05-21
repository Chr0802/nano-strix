# src/nano_strix/agents/per_file_lib/classifier.py
from __future__ import annotations

import json
import logging
import time as _time
from pathlib import Path
from typing import Any

from nano_strix.agents.per_file_lib.manifest import FileManifest

logger = logging.getLogger(__name__)

VALID_PRIORITIES = frozenset({"high", "medium", "low"})

# Directory under workspace where phase 1 intermediate outputs are saved.
PHASE1_OUTPUT_DIR = "phase1_classification"


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
        valid_dims = [d for d in dimensions if isinstance(d, str)]
        dimensions = valid_dims

    return {"priority": priority, "dimensions": dimensions}


CLASSIFIER_SYSTEM_PROMPT = """You are a code security analyst.
Your task is to classify source files by risk priority and dimension.

For each file in the provided directory listing, assign:
1. **priority**: "high" | "medium" | "low"
   - high: auth, login, database, API routes, input handling, exec
   - medium: business logic, middleware, model definitions
   - low: config, utilities, static assets, tests, fixtures
2. **dimensions**: list from ["route","dataflow","auth","dependency"]
   - route: HTTP routes or API endpoints
   - dataflow: user input, database ops, command exec, file I/O
   - auth: authentication, authorization, session, JWT, passwords
   - dependency: third-party imports, dependency declarations

Return ONLY a JSON object with "files" key mapping each file path to
{"priority": ..., "dimensions": [...]}.
Do NOT include any other text."""


async def classify_files(
    target_dir: str,
    manifest_path: Path,
    llm_client,
    agent_names: list[str],
    max_file_retries: int = 3,
) -> FileManifest:
    """Phase 1: Discover files in target_dir and classify via LLM."""
    t_start = _time.monotonic()
    target = Path(target_dir)
    if not target.exists():
        raise FileNotFoundError(f"Target directory not found: {target_dir}")

    # Set up phase 1 output directory
    phase1_dir = manifest_path.parent / PHASE1_OUTPUT_DIR
    phase1_dir.mkdir(parents=True, exist_ok=True)

    # Collect all files recursively
    all_files: list[str] = []
    for p in sorted(target.rglob("*")):
        if p.is_file() and not p.name.startswith("."):
            rel = str(p.relative_to(target))
            all_files.append(rel)

    logger.info(
        "Phase 1: discovered %d files in %s (%.2fs)",
        len(all_files), target_dir, _time.monotonic() - t_start,
    )

    if not all_files:
        logger.info("Phase 1: no files found, creating empty manifest")
        return FileManifest.create(
            path=manifest_path,
            files={},
            agent_names=agent_names,
            max_file_retries=max_file_retries,
        )

    # Save discovered file list
    (phase1_dir / "discovered_files.json").write_text(json.dumps({
        "target_dir": str(target_dir), "files": all_files,
    }, indent=2, ensure_ascii=False))

    # Build prompt
    file_list = "\n".join(f"  - {f}" for f in all_files)
    user_prompt = f"Directory: {target_dir}\nFiles ({len(all_files)}):\n{file_list}"

    # Save the LLM prompt for debugging
    try:
        prompt_data = {
            "system": CLASSIFIER_SYSTEM_PROMPT,
            "user": user_prompt,
            "model": str(getattr(llm_client, "model", "unknown")),
        }
        (phase1_dir / "llm_prompt.json").write_text(
            json.dumps(prompt_data, indent=2, ensure_ascii=False)
        )
    except Exception:
        logger.debug("Could not save phase 1 LLM prompt", exc_info=True)

    logger.info("Phase 1: sending classification request (%d files, ~%d chars prompt)",
                 len(all_files), len(user_prompt))

    t_llm = _time.monotonic()
    response = await llm_client.chat(
        messages=[
            {"role": "system", "content": CLASSIFIER_SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.1,
        max_tokens=8192,
    )
    llm_elapsed = _time.monotonic() - t_llm
    try:
        in_tokens = response.usage.get("input_tokens", 0) if response.usage else 0
        out_tokens = response.usage.get("output_tokens", 0) if response.usage else 0
    except Exception:
        in_tokens, out_tokens = 0, 0
    logger.info("Phase 1: LLM response received in %.2fs (tokens: in=%d out=%d)",
                 llm_elapsed, in_tokens, out_tokens)

    # Save raw LLM response
    raw_response = (response.content or "").strip()
    try:
        (phase1_dir / "llm_response_raw.txt").write_text(raw_response)
    except Exception:
        logger.debug("Could not save phase 1 LLM response", exc_info=True)

    # Parse LLM response
    raw = raw_response
    # Strip markdown code fences if present
    if raw.startswith("```"):
        lines = raw.split("\n")
        raw = "\n".join(lines[1:]) if lines else raw
    if raw.endswith("```"):
        raw = raw[:-3].strip()

    parse_error = None
    try:
        data = json.loads(raw)
        classified_raw = data.get("files", {})
        classified = {
            f: _validate_classified_entry(entry) for f, entry in classified_raw.items()
        }
    except json.JSONDecodeError as e:
        parse_error = str(e)
        logger.warning(
            "Phase 1: failed to parse LLM classifier response (%.2fs): %s",
            llm_elapsed, parse_error,
        )
        logger.debug("Phase 1: raw response[:500]: %s", raw_response[:500])
        # Fallback: all medium, no dimensions
        classified = {f: {"priority": "medium", "dimensions": []} for f in all_files}

    # Ensure all files are present in classified
    missing = [f for f in all_files if f not in classified]
    if missing:
        logger.warning(
            "Phase 1: %d files missing from classification, defaulting to medium",
            len(missing),
        )
    for f in all_files:
        if f not in classified:
            classified[f] = {"priority": "medium", "dimensions": []}

    # Build manifest dict from the known file list
    files_dict = {f: classified[f] for f in all_files}

    # Log classification stats
    stats = {"high": 0, "medium": 0, "low": 0}
    dim_counts: dict[str, int] = {}
    for f, meta in files_dict.items():
        stats[meta["priority"]] = stats.get(meta["priority"], 0) + 1
        for d in meta.get("dimensions", []):
            dim_counts[d] = dim_counts.get(d, 0) + 1
    logger.info(
        "Phase 1 classification: high=%d medium=%d low=%d  dims=%s  err=%s",
        stats["high"], stats["medium"], stats["low"],
        dim_counts, parse_error or "none",
    )

    # Save parsed classification result
    classification_output = {
        "stats": stats,
        "dimension_counts": dim_counts,
        "parse_error": parse_error,
        "llm_elapsed_s": round(llm_elapsed, 3),
        "files": files_dict,
    }
    try:
        (phase1_dir / "classification_result.json").write_text(
            json.dumps(classification_output, indent=2, ensure_ascii=False)
        )
    except Exception:
        logger.debug("Could not save phase 1 classification result", exc_info=True)

    manifest = FileManifest.create(
        path=manifest_path,
        files=files_dict,
        agent_names=agent_names,
        max_file_retries=max_file_retries,
    )
    manifest.phase = "classification"
    manifest.save()

    logger.info("Phase 1 complete: %d files classified in %.2fs",
                 len(files_dict), _time.monotonic() - t_start)
    return manifest
