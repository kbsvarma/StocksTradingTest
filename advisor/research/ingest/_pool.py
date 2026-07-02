"""Shared fetch pool for the snapshotters — with the retry/backoff the
first full-universe run proved necessary (2026-07-02: estimates 0/1513 —
Yahoo hard-throttled the endpoint storm while info only half-failed).

Strategy: modest concurrency, per-call pacing, then up to `retry_rounds`
cooldown-separated retry passes over the failures at lower concurrency.
Returns (rows, n_errors, err_samples) — samples land in the manifest so a
failure mode is diagnosable the same morning, not a week later.
"""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed

PACE_S = 0.05          # per-call spacing inside a worker
COOLDOWN_S = 45


def run_pool(fn, tickers: list[str], workers: int = 4,
             retry_rounds: int = 1) -> tuple[list, int, list[str]]:
    rows: list = []
    err_samples: list[str] = []
    pending = list(tickers)

    def _wrapped(t):
        time.sleep(PACE_S)
        try:
            return t, fn(t), None
        except Exception as exc:            # fn's own try/excepts catch most;
            return t, None, f"{type(exc).__name__}: {exc}"   # this is the backstop

    for round_no in range(retry_rounds + 1):
        if not pending:
            break
        if round_no:
            time.sleep(COOLDOWN_S)          # let the throttle cool off
        w = max(2, workers - 2 * round_no)  # back off concurrency on retries
        failed: list[str] = []
        with ThreadPoolExecutor(max_workers=w) as pool:
            futs = {pool.submit(_wrapped, t): t for t in pending}
            for fut in as_completed(futs):
                t, row, err = fut.result()
                if row is None:
                    failed.append(t)
                    if err and len(err_samples) < 5:
                        err_samples.append(f"{t}: {err}")
                else:
                    rows.append(row)
        pending = failed
    return rows, len(pending), err_samples
