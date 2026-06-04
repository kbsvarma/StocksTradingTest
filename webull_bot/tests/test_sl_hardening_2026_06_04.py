"""SL-hardening regression tests — 2026-06-04 orphan incident.

These cover the failure modes found in the post-incident deep dive:

1. test_no_module_import_shadowed_in_any_function
   The ROOT CAUSE class: a module-level import re-imported inside a function
   makes the name function-local for the WHOLE function, so any code path that
   references it before the local import raises UnboundLocalError. This is what
   crashed EVERY fill before the SL monitor armed. Source-line order does NOT
   catch it (the crashing load was *after* the local import in source); the only
   safe invariant is: never re-import a module-level name locally. This scans the
   whole package and fails if the anti-pattern reappears.

2. test_run_does_not_treat_OpenPosition_as_local
   Targeted regression for the exact 2026-06-04 bug.

3. stream staleness — latest_mark() must return None on a frozen feed so the
   monitor falls back to a fresh poll instead of evaluating the stop against a
   stale price (silent SL killer).

4. close-raise — a RAISE from close_spread_market during the SL close-retry loop
   must be treated as a failed attempt (retry), not bubble out and tear down the
   whole monitor mid-stop.
"""
import ast
import pathlib
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

WBDIR = pathlib.Path(__file__).resolve().parents[1]   # webull_bot/


# ─────────────────────────────────────────────────────────────────────────────
# 1. ROOT-CAUSE CLASS: no module-level import shadowed by a function-local import
# ─────────────────────────────────────────────────────────────────────────────
def _module_imported_names(tree: ast.AST) -> set[str]:
    names = set()
    for n in tree.body:                      # module scope only
        if isinstance(n, (ast.Import, ast.ImportFrom)):
            for a in n.names:
                names.add(a.asname or a.name.split(".")[0])
    return names


def _shadowing_violations(path: pathlib.Path) -> list[str]:
    tree = ast.parse(path.read_text())
    mod = _module_imported_names(tree)
    out = []
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for n in ast.walk(fn):
            if isinstance(n, (ast.Import, ast.ImportFrom)):
                for a in n.names:
                    nm = a.asname or a.name.split(".")[0]
                    if nm in mod:
                        out.append(f"{path.name}:{n.lineno} {fn.name}() re-imports "
                                   f"module-level '{nm}' (UnboundLocalError risk)")
    return out


def test_no_module_import_shadowed_in_any_function():
    violations = []
    for p in sorted(WBDIR.glob("*.py")):
        violations += _shadowing_violations(p)
    assert not violations, (
        "Function-local re-import of a module-level name (the 2026-06-04 orphan "
        "root cause). Remove the local import — the module-level one already "
        "covers it:\n  " + "\n  ".join(violations)
    )


def test_run_does_not_treat_OpenPosition_as_local():
    """Exact 2026-06-04 regression: OpenPosition must be a global ref in run()."""
    tree = ast.parse((WBDIR / "main.py").read_text())
    run = next(n for n in ast.walk(tree)
               if isinstance(n, ast.FunctionDef) and n.name == "run")
    local_names = set()
    for n in ast.walk(run):
        if isinstance(n, (ast.Import, ast.ImportFrom)):
            for a in n.names:
                local_names.add(a.asname or a.name.split(".")[0])
        if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store):
            local_names.add(n.id)
    assert "OpenPosition" not in local_names, (
        "OpenPosition is local to run() again — fresh-entry fills will "
        "UnboundLocalError before the SL monitor arms (the 2026-06-04 bug)."
    )


# ─────────────────────────────────────────────────────────────────────────────
# 3. STREAM STALENESS — latest_mark() returns None on a frozen feed
# ─────────────────────────────────────────────────────────────────────────────
def _make_stream(short_tick, long_tick):
    from webull_bot.ibkr_market_data import SpreadStream
    s = SpreadStream(ib=None, short_contract=None, long_contract=None,
                     symbol="SPXW", expiry="2026-06-04")
    s._started = True
    s._short_ticker = short_tick
    s._long_ticker = long_tick
    return s


def _tick(bid, ask, age_seconds, tzaware=True):
    ts = datetime.now(timezone.utc) - timedelta(seconds=age_seconds)
    if not tzaware:
        ts = ts.replace(tzinfo=None)
    return SimpleNamespace(bid=bid, ask=ask, last=None, close=None,
                           time=ts, modelGreeks=None)


def test_stream_fresh_returns_mark():
    s = _make_stream(_tick(2.0, 2.2, age_seconds=1),
                     _tick(0.9, 1.1, age_seconds=1))
    # short mid 2.1, long mid 1.0 -> spread 1.1
    assert s.latest_mark() == pytest.approx(1.1, abs=0.01)


def test_stream_stale_returns_none():
    """Frozen feed (tick older than STREAM_STALE_SECONDS) -> None."""
    s = _make_stream(_tick(2.0, 2.2, age_seconds=60),     # stale
                     _tick(0.9, 1.1, age_seconds=1))       # fresh
    assert s.latest_mark() is None, "stale leg must force a fresh-poll fallback"


def test_stream_one_leg_stale_returns_none():
    s = _make_stream(_tick(2.0, 2.2, age_seconds=1),       # fresh
                     _tick(0.9, 1.1, age_seconds=999))      # very stale
    assert s.latest_mark() is None


def test_stream_naive_timestamp_handled():
    """Naive (tz-less) tick time must not crash; treated as UTC."""
    s = _make_stream(_tick(2.0, 2.2, age_seconds=1, tzaware=False),
                     _tick(0.9, 1.1, age_seconds=1, tzaware=False))
    assert s.latest_mark() == pytest.approx(1.1, abs=0.01)


def test_stream_missing_time_is_stale():
    short = SimpleNamespace(bid=2.0, ask=2.2, last=None, close=None,
                            time=None, modelGreeks=None)
    long = _tick(0.9, 1.1, age_seconds=1)
    s = _make_stream(short, long)
    assert s.latest_mark() is None


# ─────────────────────────────────────────────────────────────────────────────
# 4. CLOSE-RAISE — a raise in the SL close loop is retried, not propagated
# ─────────────────────────────────────────────────────────────────────────────
class _NullLogger:
    def info(self, *a, **k): pass
    def warning(self, *a, **k): pass
    def error(self, *a, **k): pass
    def order_event(self, *a, **k): pass
    def append_trade(self, **k): pass


class _NullStore:
    path = "/tmp/_sl_hardening_test_state.json"   # only used to derive heartbeat dir
    def save(self, state): pass


class _RaiseThenFillExec:
    """close_spread_market raises once (network), then fills."""
    account_id = "TEST"

    def __init__(self):
        self.calls = 0
        # _position_still_at_broker -> safe_call(get_account_position) -> None -> "still open"
        self.trade = SimpleNamespace(
            account_v2=SimpleNamespace(get_account_position=lambda **k: None)
        )

    def close_spread_market(self, **kw):
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("simulated network blow-up")
        return SimpleNamespace(filled=True, fill_price=2.5, status="FILLED", detail="ok")


def _open_state():
    from webull_bot.state import BotState, OpenPosition
    pos = OpenPosition(
        symbol="SPXW", expiry="2026-06-04",
        short_strike=7505.0, long_strike=7455.0, quantity=1,
        entry_credit=1.20, stop_price=2.40,
        entry_spx=7553.0, entry_vix=15.7,
        entry_ts="2026-06-04T10:32:06-04:00", client_order_id="test-cid",
    )
    st = BotState(trading_date="2026-06-04", trade_taken_today=True)
    st.open_position = pos
    return st, pos


def test_close_raise_is_retried_not_propagated(monkeypatch):
    """A RAISE from close_spread_market must be caught and retried, eventually
    filling — NOT bubble out of the monitor (which would kill SL mid-stop)."""
    from webull_bot.monitor import PositionMonitor
    ex = _RaiseThenFillExec()
    mon = PositionMonitor(
        execution=ex, store=_NullStore(), logger=_NullLogger(),
        monitor_interval_seconds=1, eod_close_time="23:59",  # far future -> no EOD bail
    )
    st, pos = _open_state()
    # Call the stop executor directly (skips streaming setup).
    outcome = mon._execute_stop(pos, st, mark=2.45,
                                trigger_ts=datetime.now(timezone.utc))
    assert ex.calls == 2, "should have retried after the raise"
    assert outcome.closed is True
    assert outcome.reason == "STOP_LOSS"
    assert outcome.exit_price == pytest.approx(2.5, abs=0.01)


# ─────────────────────────────────────────────────────────────────────────────
# 5. WATCHDOG FALSE-ORPHAN SUPPRESSION (combo+strike) — must keep REAL orphans
# ─────────────────────────────────────────────────────────────────────────────
# The bot stores positions by combo/strike, not per-leg iid, so the watchdog's
# raw iid-diffing false-flags monitored positions. _strikes_match_state suppresses
# ONLY confirmed-monitored positions; a real orphan must still alert.
OP = {"short_strike": 7505.0, "long_strike": 7455.0}


def test_watchdog_suppresses_monitored_position():
    from webull_bot.reconcile_watchdog_v2 import _strikes_match_state
    # broker combo strikes match state, exactly 2 legs -> suppress false positive
    assert _strikes_match_state(OP, [(7505.0, 7455.0)], active_leg_count=2) is True


def test_watchdog_alerts_real_orphan_different_strikes():
    from webull_bot.reconcile_watchdog_v2 import _strikes_match_state
    # a DIFFERENT position at broker -> must NOT be suppressed (real orphan)
    assert _strikes_match_state(OP, [(7600.0, 7550.0)], active_leg_count=2) is False


def test_watchdog_alerts_when_extra_legs_present():
    from webull_bot.reconcile_watchdog_v2 import _strikes_match_state
    # our spread matches but there are 4 active legs -> extras could be a real
    # orphan; do NOT suppress.
    assert _strikes_match_state(OP, [(7505.0, 7455.0)], active_leg_count=4) is False


def test_watchdog_no_suppress_without_combo_data():
    from webull_bot.reconcile_watchdog_v2 import _strikes_match_state
    # combo query failed/empty -> keep alerting (don't suppress on no evidence)
    assert _strikes_match_state(OP, [], active_leg_count=2) is False


def test_watchdog_no_suppress_when_state_strikes_missing():
    from webull_bot.reconcile_watchdog_v2 import _strikes_match_state
    assert _strikes_match_state({}, [(7505.0, 7455.0)], active_leg_count=2) is False
