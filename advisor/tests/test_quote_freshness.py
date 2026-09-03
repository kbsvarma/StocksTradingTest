from datetime import datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from advisor.quoted import Daemon, IB_UPDATE_FRESH_S, yf_quote_fresh


class _IB:
    def isConnected(self):
        return True


def _ticker():
    return SimpleNamespace(last=100.0, bid=99.9, ask=100.1, close=99.0,
                           marketDataType=1)


def test_frozen_ib_tick_is_not_retimestamped(monkeypatch):
    d = Daemon()
    d.ib = _IB()
    d.tickers["XYZ"] = _ticker()
    d.yf_cache = {}
    clock = {"now": 1000.0}
    monkeypatch.setattr("advisor.quoted.time.monotonic", lambda: clock["now"])
    d.last_update_mono["XYZ"] = clock["now"]
    original_ts = datetime.now(ZoneInfo("America/New_York")).isoformat()
    d.last_update_ts["XYZ"] = original_ts

    first = d.snapshot(["XYZ"])["quotes"]["XYZ"]
    assert first["ts"] == original_ts
    clock["now"] += IB_UPDATE_FRESH_S + 1
    assert "XYZ" not in d.snapshot(["XYZ"])["quotes"]


def test_yahoo_freshness_uses_market_timestamp_during_open_market():
    now = datetime(2026, 7, 16, 12, 0, tzinfo=ZoneInfo("America/New_York"))
    fresh = {"market_ts": (now - timedelta(minutes=10)).isoformat()}
    stale = {"market_ts": (now - timedelta(minutes=31)).isoformat()}
    assert yf_quote_fresh(fresh, now)
    assert not yf_quote_fresh(stale, now)


def test_yahoo_prior_session_allowed_when_market_closed():
    now = datetime(2026, 7, 18, 12, 0, tzinfo=ZoneInfo("America/New_York"))
    friday_close = {"market_ts": "2026-07-17T16:00:00-04:00"}
    assert yf_quote_fresh(friday_close, now)


def test_stale_yahoo_cache_is_omitted():
    d = Daemon()
    d.yf_cache["XYZ"] = {"px": 100, "market_ts": "2020-01-01T16:00:00-05:00"}
    assert "XYZ" not in d.snapshot(["XYZ"])["quotes"]
