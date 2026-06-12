"""Offline tests for executor gates — no broker, no Telegram, no orders.

Every gate fires BEFORE any webull import, so the whole refusal surface is
testable cold. Run: python advisor/tests/test_executor_gates.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

if "ADVISOR_DATA_DIR" not in os.environ:
    os.environ["ADVISOR_DATA_DIR"] = tempfile.mkdtemp(prefix="advisor_exec_test_")

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO))

from advisor import executor, proposals as P  # noqa: E402

ET = ZoneInfo("America/New_York")
WED_10AM = datetime(2026, 6, 10, 10, 0, tzinfo=ET)     # weekday, in window
SAT_10AM = datetime(2026, 6, 13, 10, 0, tzinfo=ET)     # weekend
WED_8AM = datetime(2026, 6, 10, 8, 0, tzinfo=ET)       # before window


def _fresh():
    os.environ["ADVISOR_DATA_DIR"] = tempfile.mkdtemp(prefix="advisor_exec_test_")


def _clock(dt):
    executor._now = lambda: dt          # freeze executor clock


def _mk(status="APPROVED", **kw):
    args = dict(symbol="SPXW", expiry="2026-06-10", short=7500.0, long_=7450.0,
                qty=1, limit_price=1.90, rationale="t", conviction="high")
    args.update(kw)
    # bypass proposal-time validation entirely — write the file directly so we
    # can craft states (expired/invalid) that new_proposal would refuse
    import uuid
    from dataclasses import asdict
    now = datetime(2026, 6, 10, 9, 0, tzinfo=ET)
    # TTL check (Proposal.expired) uses the REAL clock — default to a real
    # future expiry so only the explicit expiry test trips that gate
    real_future = (datetime.now(ET) + timedelta(hours=2)).isoformat()
    p = P.Proposal(
        id=uuid.uuid4().hex[:4].upper(), created_ts=now.isoformat(),
        expires_ts=kw.get("expires_ts", real_future),
        kind="BPS", symbol=args["symbol"], expiry=args["expiry"],
        short_strike=args["short"], long_strike=args["long_"], qty=args["qty"],
        limit_price=args["limit_price"], max_loss_usd=4810.0,
        rationale="t", conviction="high", status=status)
    P._save(p)
    return p


def test_unknown_proposal_refused():
    _fresh(); _clock(WED_10AM)
    assert executor.execute("ZZZZ", run_monitor=False) == 1


def test_not_approved_refused_and_state_preserved():
    _fresh(); _clock(WED_10AM)
    p = _mk(status="PENDING")
    assert executor.execute(p.id, run_monitor=False) == 1
    assert P.load(p.id).status == "PENDING"      # untouched — approvable later


def test_executed_is_idempotent():
    _fresh(); _clock(WED_10AM)
    p = _mk(status="EXECUTED")
    assert executor.execute(p.id, run_monitor=False) == 1
    assert P.load(p.id).status == "EXECUTED"     # no double-execution path


def test_expired_approval_refused():
    _fresh(); _clock(WED_10AM)
    p = _mk(expires_ts=(WED_10AM - timedelta(minutes=5)).isoformat())
    assert executor.execute(p.id, run_monitor=False) == 1
    assert P.load(p.id).status == "EXPIRED"


def test_weekend_refused():
    _fresh(); _clock(SAT_10AM)
    p = _mk()
    assert executor.execute(p.id, run_monitor=False) == 1
    assert P.load(p.id).status == "APPROVED"     # refusal ≠ failure; re-runnable


def test_before_window_refused():
    _fresh(); _clock(WED_8AM)
    p = _mk()
    assert executor.execute(p.id, run_monitor=False) == 1
    assert P.load(p.id).status == "APPROVED"


def test_daily_execution_cap():
    _fresh(); _clock(WED_10AM)
    done = _mk(status="EXECUTED")
    done.created_ts = WED_10AM.isoformat()       # count as today's execution
    done.executed_ts = WED_10AM.isoformat()
    P._save(done)
    p = _mk()
    p.created_ts = WED_10AM.isoformat(); P._save(p)
    assert executor.execute(p.id, run_monitor=False) == 1
    assert P.load(p.id).status == "APPROVED"


def test_rail_recheck_blocks_non_whitelisted():
    _fresh(); _clock(WED_10AM)
    p = _mk(symbol="SPY")                        # crafted around creation rails
    assert executor.execute(p.id, run_monitor=False) == 1
    assert P.load(p.id).status == "APPROVED"     # refused before EXECUTING


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"  ✓ {fn.__name__}")
        except AssertionError as exc:
            print(f"  ✗ {fn.__name__}: {exc}")
            failed += 1
    print(f"{len(fns) - failed}/{len(fns)} passed")
    sys.exit(1 if failed else 0)
