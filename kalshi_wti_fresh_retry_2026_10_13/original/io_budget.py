"""Scoped GET budgets. Mutating requests are never automatically retried."""
from contextlib import contextmanager
from contextvars import ContextVar
import time

_until = ContextVar('kalshi_get_until', default=None)

@contextmanager
def get_budget(seconds):
    previous = _until.get()
    end = time.monotonic() + max(0, seconds)
    token = _until.set(min(previous, end) if previous is not None else end)
    try:
        yield
    finally:
        _until.reset(token)

def remaining():
    end = _until.get()
    return None if end is None else max(0, end-time.monotonic())

def timeout(default):
    left = remaining()
    if left is not None and left <= .02:
        raise TimeoutError('GET time budget exhausted')
    return min(default, left) if left is not None else default

def retry_sleep(seconds):
    left = remaining()
    if left is not None and left <= seconds+.1:
        raise TimeoutError('GET retry would exceed time budget')
    time.sleep(seconds)
