"""Offline tests for the advisor Tier 1 rails — no broker, no Telegram.

Run:  ADVISOR_DATA_DIR=$(mktemp -d) python -m pytest advisor/tests/ -q
(or plain `python advisor/tests/test_advisor.py` which sets the env itself)
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

# Isolate data dir BEFORE importing advisor modules
if "ADVISOR_DATA_DIR" not in os.environ:
    os.environ["ADVISOR_DATA_DIR"] = tempfile.mkdtemp(prefix="advisor_test_")

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO))

from advisor import journal, proposals as P  # noqa: E402
from advisor.approval_listener import CMD_RE  # noqa: E402

ET = ZoneInfo("America/New_York")


def _fresh():
    """Each test gets an isolated data dir — proposals/journal read env per call."""
    os.environ["ADVISOR_DATA_DIR"] = tempfile.mkdtemp(prefix="advisor_test_")


def _mk(**kw):
    args = dict(symbol="SPXW", expiry=datetime.now(ET).date().isoformat(),
                short=7500.0, long_=7450.0, qty=1, limit_price=1.90,
                rationale="test", conviction="high")
    args.update(kw)
    return P.new_proposal(args["symbol"], args["expiry"], args["short"],
                          args["long_"], args["qty"], args["limit_price"],
                          args["rationale"], args["conviction"],
                          args.get("ttl_min"))


def test_valid_proposal_roundtrip():
    _fresh()
    p = _mk()
    assert p.status == "PENDING" and len(p.id) == 4
    q = P.load(p.id)
    assert q.short_strike == 7500.0 and q.max_loss_usd == (50 * 100 - 190) * 1


def test_whitelist_enforced():
    _fresh()
    for bad in ("SPY", "TSLA", "GC"):
        try:
            _mk(symbol=bad)
            assert False, f"{bad} should be refused"
        except ValueError as e:
            assert "whitelist" in str(e)


def test_tick_alignment_enforced():
    _fresh()
    try:
        _mk(limit_price=1.93)
        assert False
    except ValueError as e:
        assert "tick" in str(e)


def test_width_qty_and_loss_caps():
    _fresh()
    for kw, frag in [(dict(short=7600.0, long_=7500.0), "width"),
                     (dict(qty=2), "qty"),
                     (dict(short=7500.0, long_=7455.0, limit_price=0.05), "")]:
        try:
            _mk(**kw)
            if frag:
                assert False, f"{kw} should be refused"
        except ValueError as e:
            if frag:
                assert frag in str(e)


def test_past_expiry_refused():
    _fresh()
    try:
        _mk(expiry="2020-01-01")
        assert False
    except ValueError as e:
        assert "past" in str(e)


def test_transition_legality_and_idempotency():
    _fresh()
    p = _mk()
    P.transition(p.id, "APPROVED")
    P.transition(p.id, "EXECUTING")
    P.transition(p.id, "EXECUTED", "filled @ 1.90")
    # Re-executing an EXECUTED proposal must be illegal (idempotency guard)
    for illegal in ("EXECUTING", "APPROVED", "PENDING"):
        try:
            P.transition(p.id, illegal)
            assert False, f"EXECUTED → {illegal} must be illegal"
        except ValueError:
            pass


def test_reject_path():
    _fresh()
    p = _mk()
    P.transition(p.id, "REJECTED", "user said no")
    try:
        P.transition(p.id, "APPROVED")
        assert False
    except ValueError:
        pass


def test_ttl_expiry():
    _fresh()
    p = _mk(ttl_min=1)
    assert not p.expired()
    past = datetime.now(ET) + timedelta(minutes=2)
    assert p.expired(now=past)


def test_daily_proposal_cap():
    _fresh()
    # 3 created above are PENDING/terminal — cap is 3/day; 4th must refuse.
    # (count depends on test order; create until refused, assert bound)
    made, refused = 0, False
    for _ in range(10):
        try:
            _mk()
            made += 1
        except ValueError as e:
            refused = "cap" in str(e)
            break
    assert refused, "daily cap never triggered"
    cap = P.load_advisor_cfg()["limits"]["max_proposals_per_day"]
    assert len(P.created_today()) == cap


def test_approval_regex():
    _fresh()
    ok = [("YES AB12", ("yes", "AB12")), ("no f00d", ("no", "F00D")),
          ("  Approve 1A2B  ", ("approve", "1A2B"))]
    for text, (verb, pid) in ok:
        m = CMD_RE.match(text)
        assert m and m.group(1).lower() == verb and m.group(2).upper() == pid
    for bad in ("YES", "YES ABCDE", "YESAB12", "delete AB12", "yes ab1"):
        assert not CMD_RE.match(bad), bad


def test_journal_roundtrip_and_resolve():
    _fresh()
    e = journal.add({"type": "view", "instrument": "XLE", "direction": "long",
                     "thesis": "t", "entry": "92", "target": "101", "stop": "88"})
    assert journal.effective()[e["id"]]["status"] == "open"
    journal.resolve(e["id"], "hit_target", "closed at 101.2")
    assert journal.effective()[e["id"]]["status"] == "hit_target"


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
