"""Regressions for the ingest fetch pool's throttle detection.

Two defects this guards, both of which silently disabled generators:

  * Yahoo throttles the estimate endpoints by returning EMPTY payloads rather
    than a 429/401, so `_is_ratelimit` never matched and the pool retried at
    45s/4-workers instead of entering the penalty box. Estimates returned
    0 rows from 2026-07-31 to 2026-09-03 and the fundamental half of the
    candidate slate went dark with it.
  * Once "throttled" became a marker, a single DELISTED ticker (yfinance
    swallows the 404 and the snapshotter raises "near-empty .info — throttled?")
    was enough to buy the next round a 150s penalty cooldown.
"""
import advisor.research.ingest._pool as pool


# --- marker detection ------------------------------------------------------

def test_detects_classic_ratelimit_signatures():
    for err in ("YFRateLimitError: Too Many Requests",
                "HTTPError: 401 Unauthorized",
                "RuntimeError: invalid crumb"):
        assert pool._is_ratelimit(err)


def test_detects_empty_payload_throttling():
    """The signature that went unmatched for a month."""
    assert pool._is_ratelimit("RuntimeError: all estimate endpoints empty — throttled?")
    assert pool._is_ratelimit("RuntimeError: near-empty .info (1 keys) — throttled?")


def test_ordinary_failures_are_not_ratelimits():
    for err in ("ValueError: no data", "KeyError: 'marketCap'", None, ""):
        assert not pool._is_ratelimit(err)


# --- population-level gating ----------------------------------------------

def _run(fail_for, tickers, monkeypatch, **kw):
    """Run the pool with sleeps stubbed out; record any cooldowns taken."""
    cooldowns = []
    monkeypatch.setattr(pool.time, "sleep", lambda s: cooldowns.append(s))

    def fn(t):
        if t in fail_for:
            raise RuntimeError("near-empty .info (1 keys) — throttled?")
        return {"ticker": t}

    rows, errors, samples = pool.run_pool(fn, tickers, workers=2, **kw)
    # cooldowns are the long sleeps between rounds, not the per-call pacing
    return rows, errors, [c for c in cooldowns if c >= pool.COOLDOWN_S]


def test_one_dead_symbol_does_not_trigger_the_penalty_box(monkeypatch):
    tickers = [f"T{i}" for i in range(100)]
    rows, errors, cooldowns = _run({"T7"}, tickers, monkeypatch, retry_rounds=1)
    assert len(rows) == 99 and errors == 1
    assert cooldowns == [pool.COOLDOWN_S]        # ordinary backoff, not 150s


def test_widespread_throttling_does_trigger_the_penalty_box(monkeypatch):
    tickers = [f"T{i}" for i in range(100)]
    rows, errors, cooldowns = _run(set(tickers[:60]), tickers, monkeypatch,
                                   retry_rounds=1)
    assert errors == 60
    assert cooldowns == [pool.RATELIMIT_COOLDOWN_S]


def test_threshold_is_a_share_not_a_count(monkeypatch):
    """9 failures in 100 is noise; 9 in 20 is the penalty box."""
    big = [f"T{i}" for i in range(100)]
    _, _, cd_big = _run(set(big[:9]), big, monkeypatch, retry_rounds=1)
    assert cd_big == [pool.COOLDOWN_S]

    small = [f"T{i}" for i in range(20)]
    _, _, cd_small = _run(set(small[:9]), small, monkeypatch, retry_rounds=1)
    assert cd_small == [pool.RATELIMIT_COOLDOWN_S]


def test_successful_run_takes_no_cooldown(monkeypatch):
    rows, errors, cooldowns = _run(set(), [f"T{i}" for i in range(30)],
                                   monkeypatch, retry_rounds=2)
    assert len(rows) == 30 and errors == 0 and cooldowns == []


def test_retry_recovers_transient_failures(monkeypatch):
    """A name that fails once and succeeds on the retry must land in rows."""
    monkeypatch.setattr(pool.time, "sleep", lambda s: None)
    seen = {"T1": 0}

    def fn(t):
        if t == "T1":
            seen["T1"] += 1
            if seen["T1"] == 1:
                raise RuntimeError("YFRateLimitError: Too Many Requests")
        return {"ticker": t}

    rows, errors, _ = pool.run_pool(fn, ["T1", "T2"], workers=2, retry_rounds=1)
    assert errors == 0
    assert {r["ticker"] for r in rows} == {"T1", "T2"}


def test_pace_is_applied_per_call(monkeypatch):
    slept = []
    monkeypatch.setattr(pool.time, "sleep", lambda s: slept.append(s))
    pool.run_pool(lambda t: {"ticker": t}, ["A", "B", "C"], workers=1,
                  retry_rounds=0, pace_s=0.4)
    assert slept.count(0.4) == 3
