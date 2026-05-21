# src/nano_strix/agents/per_file_lib/scanner.py
from __future__ import annotations

import asyncio
import json
import logging
import shutil
import time as _time
from pathlib import Path

from nano_strix.agents.per_file_lib.manifest import FileManifest

logger = logging.getLogger(__name__)

PHASE2_OUTPUT_DIR = "phase2_static_scan"

# Map scanner names to their CLI invocation patterns
SCANNER_CONFIG = {
    "semgrep": {
        "binary": "semgrep",
        "args": lambda t: ["--config", "auto", "--json", "--no-git-ignore", t],
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
    t_start = _time.monotonic()
    manifest.phase = "static_scan"
    manifest.save()

    phase2_dir = manifest._path.parent / PHASE2_OUTPUT_DIR
    phase2_dir.mkdir(parents=True, exist_ok=True)

    logger.info(
        "Phase 2: starting static scans (scanners=%s, target=%s)",
        scanners, target_dir,
    )

    results_summary: dict[str, dict[str, int]] = {}

    for scanner_name in scanners:
        config = SCANNER_CONFIG.get(scanner_name)
        if not config:
            logger.warning("Phase 2: unknown scanner '%s', skipping", scanner_name)
            continue

        binary = shutil.which(config["binary"])
        if not binary:
            logger.warning(
                "Phase 2: %s not found in PATH, skipping", config["binary"]
            )
            continue

        logger.info("Phase 2: running %s on %s...", scanner_name, target_dir)
        t_scan = _time.monotonic()
        try:
            args = config["args"](target_dir)
            logger.debug(
                "Phase 2: %s command: %s %s",
                scanner_name, binary, " ".join(args),
            )

            process = await asyncio.create_subprocess_exec(
                binary,
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                process.communicate(), timeout=300
            )

            scan_elapsed = _time.monotonic() - t_scan
            stderr_text = stderr.decode(errors="replace").strip()

            if process.returncode != 0:
                if scanner_name == "semgrep":
                    if process.returncode == 1:
                        pass  # semgrep returns 1 when findings exist -- expected
                    else:
                        logger.warning(
                            "Phase 2: %s exited with code %d in %.1fs (stderr: %s)",
                            scanner_name, process.returncode, scan_elapsed,
                            stderr_text[:500] or "(empty)",
                        )
                elif scanner_name == "bandit":
                    if process.returncode >= 2:
                        logger.warning(
                            "Phase 2: %s exited with code %d in %.1fs (stderr: %s)",
                            scanner_name, process.returncode, scan_elapsed,
                            stderr_text[:500] or "(empty)",
                        )

            output = stdout.decode(errors="replace").strip()

            # Save raw scanner output
            scanner_out_dir = phase2_dir / scanner_name
            scanner_out_dir.mkdir(parents=True, exist_ok=True)
            out_file = (
                scanner_out_dir / "stdout.json"
                if output else scanner_out_dir / "stdout.txt"
            )
            out_file.write_text(output or "(empty)")
            if stderr_text:
                (scanner_out_dir / "stderr.txt").write_text(stderr_text)
            (scanner_out_dir / "meta.json").write_text(json.dumps({
                "scanner": scanner_name,
                "binary": binary,
                "args": args,
                "returncode": process.returncode,
                "elapsed_s": round(scan_elapsed, 3),
            }, indent=2))

            if not output:
                logger.info(
                    "Phase 2: %s produced no output (%.1fs)",
                    scanner_name, scan_elapsed,
                )
                results_summary[scanner_name] = {
                    "findings": 0, "elapsed_s": round(scan_elapsed, 2),
                }
                continue

            finding_count = _parse_and_apply_findings(
                manifest, scanner_name, output, target_dir,
            )
            results_summary[scanner_name] = {
                "findings": finding_count,
                "elapsed_s": round(scan_elapsed, 2),
            }
            logger.info(
                "Phase 2: %s found %d issues in %.1fs",
                scanner_name, finding_count, scan_elapsed,
            )

        except asyncio.TimeoutError:
            logger.warning("Phase 2: %s timed out after 300s", scanner_name)
            try:
                process.kill()
            except ProcessLookupError:
                pass
            await process.wait()
            results_summary[scanner_name] = {
                "findings": 0, "elapsed_s": 300, "error": "timeout",
            }
        except Exception:
            logger.exception("Phase 2: error running %s", scanner_name)
            results_summary[scanner_name] = {
                "findings": 0, "elapsed_s": 0, "error": "exception",
            }

    # Save summary
    (phase2_dir / "summary.json").write_text(json.dumps({
        "scanners": list(results_summary.keys()),
        "results": results_summary,
        "total_elapsed_s": round(_time.monotonic() - t_start, 3),
    }, indent=2, ensure_ascii=False))

    manifest.phase = "static_scan"
    manifest.save()
    logger.info(
        "Phase 2 complete: %d scanners finished in %.1fs, %d total findings",
        len(results_summary),
        _time.monotonic() - t_start,
        sum(r.get("findings", 0) for r in results_summary.values()),
    )


def _parse_and_apply_findings(
    manifest: FileManifest,
    scanner_name: str,
    output: str,
    target_dir: str,
) -> int:
    """Parse scanner JSON output and attach findings to manifest files.

    Returns the number of findings parsed.
    """
    try:
        data = json.loads(output)
    except json.JSONDecodeError:
        logger.debug("Phase 2: could not parse %s output as JSON", scanner_name)
        return 0

    target = Path(target_dir)
    count = 0

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
                count += 1
            else:
                logger.debug(
                    "Phase 2: %s finding for '%s' skipped: file not in manifest",
                    scanner_name, rel_path,
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
                count += 1
            else:
                logger.debug(
                    "Phase 2: %s finding for '%s' skipped: file not in manifest",
                    scanner_name, rel_path,
                )

    manifest.save()
    return count
