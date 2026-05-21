"""Shared logging setup for nano-strix agents and CLI.

Applies ``LoggingConfig`` from config.yaml to Python's logging system.
Every agent stub (per_file, cross_file, etc.) should call ``setup_logging``
early in its entry point so log output is consistent across subprocesses.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import TextIO

from nano_strix.config.schema import LoggingConfig

DEFAULT_FORMAT = "%(asctime)s [%(name)s] %(levelname)s %(message)s"
DEFAULT_DATE_FORMAT = "%H:%M:%S"


def setup_logging(
    config: LoggingConfig | None = None,
    *,
    log_file: Path | None = None,
    stream: TextIO | None = sys.stderr,
    force: bool = False,
) -> None:
    """Configure the Python logging root logger.

    Args:
        config: ``LoggingConfig`` from config.yaml. If None, defaults to INFO.
        log_file: Optional file path for persistent log output.
        stream: Stream for console output (default stderr to keep stdout clean for IPC).
        force: Reconfigure even if handlers already exist.
    """
    level_name = config.level.upper() if config and config.level else "INFO"
    level = getattr(logging, level_name, logging.INFO)

    root = logging.getLogger()
    root.setLevel(level)

    # Remove existing handlers if forcing reconfiguration
    if force:
        for h in list(root.handlers):
            root.removeHandler(h)

    if root.handlers:
        return

    formatter = logging.Formatter(DEFAULT_FORMAT, DEFAULT_DATE_FORMAT)

    # Stream handler (stderr by default)
    if stream:
        stream_handler = logging.StreamHandler(stream)
        stream_handler.setLevel(level)
        stream_handler.setFormatter(formatter)
        root.addHandler(stream_handler)

    # File handler
    if log_file:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(str(log_file), encoding="utf-8")
        file_handler.setLevel(logging.DEBUG)  # File always gets DEBUG
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)

    # Apply per-category levels from config
    if config and config.categories:
        for category_name, cat_level_name in config.categories.items():
            cat_level = getattr(logging, cat_level_name.upper(), None)
            if cat_level is not None:
                logging.getLogger(category_name).setLevel(cat_level)

    # Quiet noisy third-party loggers
    for noisy in ("httpx", "httpcore", "urllib3", "anthropic"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
