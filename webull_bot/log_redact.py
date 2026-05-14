"""Process-wide logging filter that scrubs known secrets from log records.

Belt-and-suspenders against secrets leaking into stdout/stderr/SDK log files.
Importing this module installs a Filter on the root logger AND the
`webullsdkcore` logger, so any record those see — regardless of which handler
later writes it — gets its message + args scrubbed before formatting.

What gets scrubbed:
- Webull APP_KEY/APP_SECRET prefixes (catches both the literal known values and
  anything passed as substring in headers like x-app-key/x-signature)
- Telegram bot token prefix
- Anything from the WEBULL_APP_KEY/SECRET/TELEGRAM_BOT_TOKEN env vars at import
  time (so rotated keys are also scrubbed if .webull_env is sourced first)

Failure mode: filter never raises — on any error it returns the record
untouched (logging itself must never break the bot).

Usage:
    import webull_bot.log_redact  # noqa: F401  — side-effect: install filter
"""
from __future__ import annotations

import logging
import os
from typing import Iterable

# Hardcoded prefixes (the leaked-once values the audit found in mode-644 logs).
# These remain so historical key prefixes stay redacted even after rotation,
# in case a bug somewhere logs an old value from cache.
_HARDCODED_PREFIXES = (
    "e97032ca51136cae",      # Webull APP_KEY (leaked, must be rotated)
    "7898b703c3a902a1",      # Webull APP_SECRET (leaked, must be rotated)
    "8842241204:AAFsVnZZ",   # Telegram bot token (leaked, must be rotated)
)

# Env-derived secrets — picked up at import time so newly-rotated keys are
# scrubbed without code changes.
_ENV_VARS = (
    "WEBULL_APP_KEY",
    "WEBULL_APP_SECRET",
    "TELEGRAM_BOT_TOKEN",
)


def _gather_prefixes() -> tuple[str, ...]:
    p = list(_HARDCODED_PREFIXES)
    for v in _ENV_VARS:
        val = (os.environ.get(v) or "").strip()
        # Only scrub values long enough to be unique-ish; avoids accidental
        # mass-redaction if an env var is set to "x" or empty.
        if len(val) >= 12:
            p.append(val)
    # Dedup, longest-first so substring scrubs happen on the longest match.
    return tuple(sorted(set(p), key=len, reverse=True))


_REPLACEMENT = "<REDACTED>"


def _scrub_str(s: str, prefixes: Iterable[str]) -> str:
    for p in prefixes:
        if p and p in s:
            s = s.replace(p, _REPLACEMENT)
    return s


def _scrub_obj(obj, prefixes: Iterable[str]):
    if isinstance(obj, str):
        return _scrub_str(obj, prefixes)
    if isinstance(obj, (list, tuple)):
        scrubbed = [_scrub_obj(x, prefixes) for x in obj]
        return type(obj)(scrubbed) if isinstance(obj, tuple) else scrubbed
    if isinstance(obj, dict):
        return {k: _scrub_obj(v, prefixes) for k, v in obj.items()}
    return obj


class _SecretScrubFilter(logging.Filter):
    def __init__(self) -> None:
        super().__init__()
        self._prefixes = _gather_prefixes()

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            if isinstance(record.msg, str):
                record.msg = _scrub_str(record.msg, self._prefixes)
            if record.args:
                record.args = _scrub_obj(record.args, self._prefixes)
        except Exception:
            pass  # never break logging
        return True


_INSTALLED = False


def install() -> None:
    """Idempotently install the scrub filter on root + webullsdkcore loggers."""
    global _INSTALLED
    if _INSTALLED:
        return
    f = _SecretScrubFilter()
    # Cover both SDK namespaces seen in the wild:
    #   Mac venv: webullsdkcore / webullsdktrade / webullsdkmdata
    #   EC2 venv: webull.core    / webull.trade    / webull.data
    # Both ship as `webull-openapi-python-sdk==2.0.7` but with different layouts.
    for name in (
        "",
        "webullsdkcore", "webullsdktrade", "webullsdkmdata",
        "webull", "webull.core", "webull.trade", "webull.data",
    ):
        lg = logging.getLogger(name)
        # Avoid stacking duplicates if install() somehow runs twice in same proc
        if not any(isinstance(x, _SecretScrubFilter) for x in lg.filters):
            lg.addFilter(f)
    _INSTALLED = True


# Install on import so callers only need to `import webull_bot.log_redact`
install()
