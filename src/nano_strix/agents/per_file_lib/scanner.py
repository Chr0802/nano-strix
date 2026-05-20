# src/nano_strix/agents/per_file_lib/scanner.py
from __future__ import annotations

import asyncio
import json
import logging
import shutil
from pathlib import Path

from nano_strix.agents.per_file_lib.manifest import FileManifest

logger = logging.getLogger(__name__)

# Map scanner names to their CLI invocation patterns
SCANNER_CONFIG = {
    "semgrep": {
        "binary": "semgrep",
        "args": lambda target: ["--config", "auto", "--json", "--no-git-ignore", target],
        "output_mode": "json",
    },
    "bandit": {
        "binary": "bandit",
        "args": lambda target: ["-r", target, "-f", "json"],
        "output_mode": "json",
    },
}


async def run_static_scans(
    manifest: FileManifest,
    target_dir: str,
    scanners: list[str],
) -> None:
    """Phase 2: Run static analysis tools against target directory."""
    manifest.phase = "static_scan"
    manifest.save()

    for scanner_name in scanners:
        config = SCANNER_CONFIG.get(scanner_name)
        if not config:
            logger.warning("Unknown scanner '%s', skipping", scanner_name)
            continue

        binary = shutil.which(config["binary"])
        if not binary:
            logger.warning("%s not found in PATH, skipping", config["binary"])
            continue

        logger.info("Running %s on %s...", scanner_name, target_dir)
        try:
            args = config["args"](target_dir)
            process = await asyncio.create_subprocess_exec(
                binary,
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                process.communicate(), timeout=300
            )

            if process.returncode != 0:
                if scanner_name == "semgrep":
                    if process.returncode == 1:
                        # semgrep returns 1 when findings exist -- that's expected
                        pass
                    else:
                        logger.warning(
                            "%s exited with code %d (stderr: %s)",
                            scanner_name,
                            process.returncode,
                            stderr.decode(errors="replace").strip() or "(empty)",
                        )
                elif scanner_name == "bandit":
                    if process.returncode >= 2:
                        logger.warning(
                            "%s exited with code %d (stderr: %s)",
                            scanner_name,
                            process.returncode,
                            stderr.decode(errors="replace").strip() or "(empty)",
                        )

            output = stdout.decode(errors="replace").strip()
            if not output:
                logger.info("%s produced no output", scanner_name)
                continue

            _parse_and_apply_findings(manifest, scanner_name, output, target_dir)

        except asyncio.TimeoutError:
            logger.warning("%s timed out after 300s", scanner_name)
            try:
                process.kill()
            except ProcessLookupError:
                pass
            await process.wait()
        except Exception:
            logger.exception("Error running %s", scanner_name)

    manifest.phase = "static_scan"
    manifest.save()
    logger.info("Phase 2 complete: static scans finished")


def _parse_and_apply_findings(
    manifest: FileManifest,
    scanner_name: str,
    output: str,
    target_dir: str,
) -> None:
    """Parse scanner JSON output and attach findings to manifest files."""
    try:
        data = json.loads(output)
    except json.JSONDecodeError:
        logger.debug("Could not parse %s output as JSON", scanner_name)
        return

    target = Path(target_dir)

    if scanner_name == "semgrep":
        results = data.get("results", [])
        for result in results:
            file_path = result.get("path", "")
            try:
                rel_path = str(Path(file_path).relative_to(target))
            except ValueError:
                rel_path = file_path

            check = result.get("check_id", result.get("rule", ""))
            extra = result.get("extra", {})
            finding = {
                "scanner": "semgrep",
                "rule": check,
                "line": result.get("start", {}).get("line", 0),
                "severity": extra.get("severity", "medium"),
                "message": extra.get("message", ""),
                "category": extra.get("metadata", {}).get("category", ""),
            }
            if rel_path in manifest.files:
                manifest.files[rel_path].scan_findings.append(finding)
            else:
                logger.debug(
                    "%s finding for '%s' skipped: file not in manifest",
                    scanner_name,
                    rel_path,
                )

    elif scanner_name == "bandit":
        results = data.get("results", [])
        for result in results:
            file_path = result.get("filename", "")
            try:
                rel_path = str(Path(file_path).relative_to(target))
            except ValueError:
                rel_path = file_path

            finding = {
                "scanner": "bandit",
                "rule": result.get("test_id", ""),
                "line": result.get("line_number", 0),
                "severity": result.get("issue_severity", "medium"),
                "message": result.get("issue_text", ""),
                "confidence": result.get("issue_confidence", ""),
            }
            if rel_path in manifest.files:
                manifest.files[rel_path].scan_findings.append(finding)
            else:
                logger.debug(
                    "%s finding for '%s' skipped: file not in manifest",
                    scanner_name,
                    rel_path,
                )

    manifest.save()
