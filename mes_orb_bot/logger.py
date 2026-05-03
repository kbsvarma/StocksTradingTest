# logger.py — Console + daily file logging with state/event context fields.

import logging
import os
from datetime import datetime
from zoneinfo import ZoneInfo

import config


def setup_logger(name: str = "mes_orb_bot") -> logging.Logger:
    """
    Create and return the bot logger.
    Console: INFO and above.
    File:    DEBUG and above (maximum detail for post-session analysis).
    Safe to call multiple times — handlers are added only once.
    """
    tz = ZoneInfo(config.TIMEZONE)
    today = datetime.now(tz).strftime("%Y%m%d")
    os.makedirs(config.LOG_DIR, exist_ok=True)
    log_file = os.path.join(config.LOG_DIR, f"{today}.log")

    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)

    if logger.handlers:
        return logger  # already configured

    fmt = logging.Formatter(
        "[%(asctime)s.%(msecs)03d ET] [%(state)-18s] [%(event)-24s] %(message)s",
        datefmt="%H:%M:%S",
    )

    # Console — INFO so operators can watch cleanly
    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)
    ch.setFormatter(fmt)
    logger.addHandler(ch)

    # File — DEBUG for every bar, every eval, every latency measurement
    fh = logging.FileHandler(log_file, encoding="utf-8", mode="a")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    return logger


# ── Convenience wrappers ───────────────────────────────────────────────────────
# All log calls go through these so state/event are always present in the record.

def _emit(logger: logging.Logger, level: str, state: str, event: str, msg: str) -> None:
    extra = {"state": state, "event": event}
    getattr(logger, level)(msg, extra=extra)


def log_debug(logger: logging.Logger, state: str, event: str, msg: str) -> None:
    _emit(logger, "debug", state, event, msg)


def log_info(logger: logging.Logger, state: str, event: str, msg: str) -> None:
    _emit(logger, "info", state, event, msg)


def log_warning(logger: logging.Logger, state: str, event: str, msg: str) -> None:
    _emit(logger, "warning", state, event, msg)


def log_error(logger: logging.Logger, state: str, event: str, msg: str) -> None:
    _emit(logger, "error", state, event, msg)


def log_critical(logger: logging.Logger, state: str, event: str, msg: str) -> None:
    _emit(logger, "critical", state, event, msg)
