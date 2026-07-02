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

PACE_S = 0.15          # per-call spacing inside a worker (~aggregate <10 req/s)
COOLDOWN_S = 45
RATELIMIT_COOLDOWN_S = 150     # Yahoo penalty box needs real time, not 45s

_RL_MARKERS = ("ratelimit", "too many requests", "401", "crumb", "unauthorized")


def _is_ratelimit(err: str | None) -> bool:
    return bool(err) and any(m in err.lower() for m in _RL_MARKERS)


def run_pool(fn, tickers: list[str], workers: int = 4,
             retry_rounds: int = 1, pace_s: float = PACE_S
             ) -> tuple[list, int, list[str]]:
    rows: list = []
    err_samples: list[str] = []
    pending = list(tickers)
    hit_ratelimit = False

    def _wrapped(t):
        time.sleep(pace_s)
        try:
            return t, fn(t), None
        except Exception as exc:            # fn's own try/excepts catch most;
            return t, None, f"{type(exc).__name__}: {exc}"   # this is the backstop

    for round_no in range(retry_rounds + 1):
        if not pending:
            break
        if round_no:
            cd = RATELIMIT_COOLDOWN_S if hit_ratelimit else COOLDOWN_S
            print(f"[pool] retry round {round_no}: {len(pending)} pending, "
                  f"cooldown {cd}s{' (rate-limited)' if hit_ratelimit else ''}",
                  flush=True)
            time.sleep(cd)
        # rate-limited → crawl on the retry; otherwise gentle backoff
        w = 2 if (round_no and hit_ratelimit) else max(2, workers - 2 * round_no)
        failed: list[str] = []
        with ThreadPoolExecutor(max_workers=w) as pool:
            futs = {pool.submit(_wrapped, t): t for t in pending}
            for fut in as_completed(futs):
                t, row, err = fut.result()
                if row is None:
                    failed.append(t)
                    hit_ratelimit = hit_ratelimit or _is_ratelimit(err)
                    if err and len(err_samples) < 5:
                        err_samples.append(f"{t}: {err}")
                else:
                    rows.append(row)
        pending = failed
    return rows, len(pending), err_samples
