"""Rotating file logger. Writes to the app's own folder, never to system logs."""
from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler

from printpal.config import _CONFIG_DIR


_LOG_DIR = _CONFIG_DIR / "logs"
_LOG_FILE = _LOG_DIR / "printpal.log"
_MAX_BYTES = 512 * 1024  # 512 KB per file
_BACKUP_COUNT = 3

_logger: logging.Logger | None = None


def get_logger() -> logging.Logger:
    global _logger
    if _logger is not None:
        return _logger

    _LOG_DIR.mkdir(parents=True, exist_ok=True)

    _logger = logging.getLogger("printpal")
    _logger.setLevel(logging.DEBUG)

    handler = RotatingFileHandler(
        str(_LOG_FILE), maxBytes=_MAX_BYTES, backupCount=_BACKUP_COUNT, encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)-5s %(message)s", datefmt="%Y-%m-%d %H:%M:%S",
    ))
    _logger.addHandler(handler)
    return _logger
