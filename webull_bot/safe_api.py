"""Shared API call helper with 429 backoff.

Wraps any Webull SDK call. On HTTP 429 or TOO_MANY_REQUESTS exception, sleeps
with exponential backoff and retries. After exhausting retries, raises or
returns the last failed response (caller's choice via raise_on_fail).

Usage:
    from webull_bot.safe_api import safe_call

    r = safe_call(tc.account_v2.get_account_position, account_id=acct)
    # r is the response object (or None if all retries failed)
"""
from __future__ import annotations

import logging
import time
from typing import Any, Callable, Optional

log = logging.getLogger(__name__)

DEFAULT_RETRIES = 3
DEFAULT_BACKOFFS = (1.0, 2.0, 4.0)  # seconds


def safe_call(
    fn: Callable[..., Any],
    *args,
    retries: int = DEFAULT_RETRIES,
    backoffs: tuple = DEFAULT_BACKOFFS,
    raise_on_fail: bool = False,
    **kwargs,
) -> Optional[Any]:
    """Call fn(*args, **kwargs) with 429-aware retry.

    Returns the response on success (status_code attribute checked).
    Returns the last response on retries exhausted (caller can inspect).
    If raise_on_fail=True, raises after all retries fail.
    """
    fn_name = getattr(fn, "__name__", "fn")
    last_response = None
    last_exception = None
    for attempt in range(retries):
        if attempt > 0:
            wait = backoffs[min(attempt - 1, len(backoffs) - 1)]
            log.info(f"safe_call retry {attempt+1}/{retries} after {wait}s sleep")
            time.sleep(wait)
        try:
            r = fn(*args, **kwargs)
            last_response = r
            status = getattr(r, "status_code", None)
            if status is None:
                return r  # no status_code attribute — assume OK
            if status == 200 or status == 201:
                return r
            if status == 429:
                log.warning(f"safe_call: HTTP 429 on {fn_name} (attempt {attempt+1}/{retries})")
                continue
            return r
        except Exception as exc:
            last_exception = exc
            msg = str(exc)
            if "429" in msg or "TOO_MANY_REQUESTS" in msg:
                log.warning(f"safe_call: 429 exception on {fn_name} (attempt {attempt+1}/{retries}): {exc}")
                continue
            raise
    if raise_on_fail:
        if last_exception:
            raise last_exception
        raise RuntimeError(f"safe_call exhausted {retries} retries for {fn_name}, last status={getattr(last_response,'status_code','?')}")
    log.warning(f"safe_call: exhausted {retries} retries for {fn_name}")
    return last_response
