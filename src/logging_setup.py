"""
Logging that writes to both console and a dated file.

Why: your snapshot collector runs unattended on a cron. When it silently
stops working three days in, the log file is the only way you'll find out
what happened — and by then the missing days are unrecoverable.
"""
from __future__ import annotations

import logging
import sys
from datetime import date

from src.config import LOGS_DIR

_CONFIGURED: set[str] = set()


def get_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if name in _CONFIGURED:
        return logger

    logger.setLevel(logging.INFO)
    logger.propagate = False

    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(fmt)
    logger.addHandler(console)

    logfile = LOGS_DIR / f"{date.today().isoformat()}.log"
    fileh = logging.FileHandler(logfile, encoding="utf-8")
    fileh.setFormatter(fmt)
    logger.addHandler(fileh)

    _CONFIGURED.add(name)
    return logger
