"""Application-wide logging configuration.

Centralizes loguru setup so every module imports the same configured logger
via ``from loguru import logger``. Keeping this here matches the architecture
doc: ``utils`` owns the ``Logger`` responsibility.
"""
from __future__ import annotations

import sys
from pathlib import Path

from loguru import logger

_LOG_FORMAT = (
    "{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | "
    "{name}:{function}:{line} - {message}"
)


def setup_logging(
    log_dir: Path = Path("logs"),
    log_file: str = "app.log",
    level: str = "INFO",
) -> None:
    """Configure loguru: stderr + rotating file sink under ``log_dir``.

    The default stderr sink that loguru adds on import is removed so we do not
    emit duplicate records. File output is plain (no ANSI colors).
    """
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / log_file

    logger.remove()
    logger.add(
        sys.stderr,
        format=_LOG_FORMAT,
        level=level,
        colorize=True,
    )
    logger.add(
        log_path,
        format=_LOG_FORMAT,
        level=level,
        rotation="10 MB",
        retention="14 days",
        encoding="utf-8",
        enqueue=True,
    )


__all__ = ["logger", "setup_logging"]
