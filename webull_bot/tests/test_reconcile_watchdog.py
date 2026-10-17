"""Tests for reconcile_watchdog.py"""
from __future__ import annotations

import json
from pathlib import Path
import pytest


class TestReconcileWatchdog:
    def test_in_market_hours(self):
        from datetime import datetime
        from zoneinfo import ZoneInfo
        from webull_bot.reconcile_watchdog import _in_market_hours, ET
        # Monday 10:30 ET → in hours
        assert _in_market_hours(datetime(2026, 5, 18, 10, 30, tzinfo=ET))
        # Monday 8:00 ET → out
        assert not _in_market_hours(datetime(2026, 5, 18, 8, 0, tzinfo=ET))
        # Saturday → out
        assert not _in_market_hours(datetime(2026, 5, 23, 12, 0, tzinfo=ET))
        # Sunday → out
        assert not _in_market_hours(datetime(2026, 5, 24, 12, 0, tzinfo=ET))

    def test_state_position_keys_empty_state(self, tmp_path):
        from webull_bot.reconcile_watchdog import _state_position_keys
        # Missing file
        assert _state_position_keys(tmp_path / "missing.json") == set()
        # Empty state
        p = tmp_path / "state.json"
        p.write_text(json.dumps({}))
        assert _state_position_keys(p) == set()
        # No open_position
        p.write_text(json.dumps({"open_position": None}))
        assert _state_position_keys(p) == set()

    def test_state_position_keys_with_position(self, tmp_path):
        from webull_bot.reconcile_watchdog import _state_position_keys
        p = tmp_path / "state.json"
        p.write_text(json.dumps({
            "open_position": {
                "short_iid": "iid-S",
                "long_iid": "iid-L",
                "short_strike": 5800,
                "long_strike": 5750,
            }
        }))
        keys = _state_position_keys(p)
        assert "iid-S" in keys
        assert "iid-L" in keys

    def test_load_config_resolves_relative_state_file(self):
        """T2.3 bug fix: watchdog should work regardless of cwd."""
        from webull_bot.reconcile_watchdog import _load_config
        cfg = _load_config()
        assert Path(cfg["state_file"]).is_absolute()
        # And the path should end with the config-specified relative bit
        assert "vG_spx_w50_vix25" in cfg["state_file"]

    def test_load_seen_handles_missing_file(self, monkeypatch, tmp_path):
        from webull_bot import reconcile_watchdog as rw
        monkeypatch.setattr(rw, "SEEN_FILE", tmp_path / "nope.json")
        seen = rw._load_seen()
        assert seen == {"orphans": {}, "phantoms": {}}

    def test_load_seen_handles_corrupt_file(self, monkeypatch, tmp_path):
        from webull_bot import reconcile_watchdog as rw
        bad = tmp_path / "bad.json"
        bad.write_text("not json {{{")
        monkeypatch.setattr(rw, "SEEN_FILE", bad)
        # Must not raise — returns default
        seen = rw._load_seen()
        assert seen == {"orphans": {}, "phantoms": {}}

    def test_check_once_no_positions_writes_ok_status(
        self, mock_tc, monkeypatch, tmp_path,
    ):
        """When broker & state both empty → status=ok, no alerts."""
        from webull_bot import reconcile_watchdog as rw

        # Sandbox the seen + status files
        monkeypatch.setattr(rw, "SEEN_FILE", tmp_path / "seen.json")
        monkeypatch.setattr(rw, "STATUS_FILE", tmp_path / "status.json")
        # State file: empty
        state_file = tmp_path / "state.json"
        state_file.write_text(json.dumps({"open_position": None}))
        cfg = {"state_file": str(state_file), "account_id": "TEST"}

        # Mock build_trade_client and ExecutionEngine
        import webull_bot.client as client_mod
        monkeypatch.setattr(client_mod, "build_trade_client", lambda: mock_tc)
        # Broker has no SPXW positions
        mock_tc.holdings = []

        alerts = []
        import webull_bot.alerts as alerts_mod
        monkeypatch.setattr(alerts_mod, "send_alert",
                            lambda text, *a, **kw: alerts.append(text) or True)

        seen = {"orphans": {}, "phantoms": {}}
        rw._check_once(cfg, seen)

        # Status file written, status=ok
        assert (tmp_path / "status.json").exists()
        status = json.loads((tmp_path / "status.json").read_text())
        assert status["status"] == "ok"
        # No alerts fired
        assert alerts == []

    def test_paper_mode_skips_broker_query_writes_ok(
        self, mock_tc, monkeypatch, tmp_path,
    ):
        """On paper instance, watchdog must NOT detect Mac live's positions
        as orphans (both share the same Webull account). Always write status=ok."""
        from webull_bot import reconcile_watchdog as rw
        monkeypatch.setattr(rw, "PAPER_MODE", True)
        monkeypatch.setattr(rw, "STATUS_FILE", tmp_path / "status.json")
        state_file = tmp_path / "state.json"
        state_file.write_text(json.dumps({"open_position": None}))
        cfg = {"state_file": str(state_file), "account_id": "TEST"}

        # Even with REAL positions at broker, paper mode must skip and write ok
        mock_tc.holdings = [
            {"instrument_id": "MAC-LIVE-iid", "symbol": "SPXW",
             "instrument_type": "OPTION", "qty": -1,
             "option_expire_date": "2099-12-31"},
        ]
        # Sabotage build_trade_client — paper mode should NEVER call it
        import webull_bot.client as client_mod
        def _fail(*a, **kw):
            raise AssertionError("paper mode should not query broker")
        monkeypatch.setattr(client_mod, "build_trade_client", _fail)

        alerts = []
        import webull_bot.alerts as alerts_mod
        monkeypatch.setattr(alerts_mod, "send_alert",
                            lambda text, *a, **kw: alerts.append(text) or True)

        rw._check_once(cfg, {"orphans": {}, "phantoms": {}})

        status = json.loads((tmp_path / "status.json").read_text())
        assert status["status"] == "ok"
        assert status["orphans"] == []
        assert "paper" in status["note"].lower()
        assert alerts == []

    def test_today_zero_dte_filtered_after_close(
        self, mock_tc, monkeypatch, tmp_path,
    ):
        """Today's 0DTE contracts sitting in holdings post-4:15 PM ET are
        expired-but-not-cleared, NOT orphans. They must be filtered."""
        from webull_bot import reconcile_watchdog as rw
        from datetime import datetime, time as dtime
        from zoneinfo import ZoneInfo

        # Pretend it's 5:00 PM ET today
        et = ZoneInfo("America/New_York")
        fixed_now = datetime.now(et).replace(hour=17, minute=0, second=0, microsecond=0)
        class _FakeDT(datetime):
            @classmethod
            def now(cls, tz=None): return fixed_now if tz is None else fixed_now.astimezone(tz)
        monkeypatch.setattr(rw, "datetime", _FakeDT)
        monkeypatch.setattr(rw, "PAPER_MODE", False)
        monkeypatch.setattr(rw, "STATUS_FILE", tmp_path / "status.json")
        state_file = tmp_path / "state.json"
        state_file.write_text(json.dumps({"open_position": None}))
        cfg = {"state_file": str(state_file), "account_id": "TEST"}

        # Holdings: one today-expiry (should be filtered post-4:15), one tomorrow (active)
        today_iso = fixed_now.date().isoformat()
        from datetime import timedelta
        tomorrow_iso = (fixed_now.date() + timedelta(days=1)).isoformat()
        mock_tc.holdings = [
            # Today's 0DTE — expired post-4:15pm but has nonzero price (filter
            # by expiry date, NOT by price, to isolate this test's signal)
            {"instrument_id": "TODAY-iid", "symbol": "SPXW",
             "instrument_type": "OPTION", "qty": -1,
             "option_expire_date": today_iso,
             "last_price": "0.50", "market_value": "50.00"},
            # Tomorrow expiry — still active
            {"instrument_id": "TOMORROW-iid", "symbol": "SPXW",
             "instrument_type": "OPTION", "qty": -1,
             "option_expire_date": tomorrow_iso,
             "last_price": "0.50", "market_value": "50.00"},
        ]
        import webull_bot.client as client_mod
        monkeypatch.setattr(client_mod, "build_trade_client", lambda: mock_tc)

        alerts = []
        import webull_bot.alerts as alerts_mod
        monkeypatch.setattr(alerts_mod, "send_alert",
                            lambda text, *a, **kw: alerts.append(text) or True)

        rw._check_once(cfg, {"orphans": {}, "phantoms": {}})
        status = json.loads((tmp_path / "status.json").read_text())
        # Only the tomorrow-expiry should appear as orphan; today-expiry filtered
        orphan_iids = [o["iid"] for o in status["orphans"]]
        assert "TODAY-iid" not in orphan_iids, \
            "REGRESSION: today's settled 0DTE leaking through as orphan"
        assert "TOMORROW-iid" in orphan_iids, \
            "real future-expiry orphan should still be detected"

    def test_worthless_holdings_filtered_no_expiry_field(
        self, mock_tc, monkeypatch, tmp_path,
    ):
        """Webull holdings has no expire_date field. Watchdog must use
        last_price + market_value as proxy: < $0.05 = expired/worthless,
        skip from orphan check."""
        from webull_bot import reconcile_watchdog as rw
        monkeypatch.setattr(rw, "PAPER_MODE", False)
        monkeypatch.setattr(rw, "STATUS_FILE", tmp_path / "status.json")
        state_file = tmp_path / "state.json"
        state_file.write_text(json.dumps({"open_position": None}))
        cfg = {"state_file": str(state_file), "account_id": "TEST"}

        mock_tc.holdings = [
            # Expired worthless: last_price=$0.01, market_value=$1 → filter
            {"instrument_id": "EXPIRED-iid", "symbol": "SPXW",
             "instrument_type": "OPTION", "qty": -1,
             "last_price": "0.01", "market_value": "1.00"},
            # Active real position: last_price=$0.50, market_value=$50 → keep
            {"instrument_id": "ACTIVE-iid", "symbol": "SPXW",
             "instrument_type": "OPTION", "qty": -1,
             "last_price": "0.50", "market_value": "50.00"},
        ]
        import webull_bot.client as client_mod
        monkeypatch.setattr(client_mod, "build_trade_client", lambda: mock_tc)
        import webull_bot.alerts as alerts_mod
        monkeypatch.setattr(alerts_mod, "send_alert", lambda *a, **k: True)

        rw._check_once(cfg, {"orphans": {}, "phantoms": {}})
        status = json.loads((tmp_path / "status.json").read_text())
        orphan_iids = [o["iid"] for o in status["orphans"]]
        assert "EXPIRED-iid" not in orphan_iids, \
            "REGRESSION: worthless ($0.01) holding leaking through as orphan"
        assert "ACTIVE-iid" in orphan_iids, \
            "real active-priced orphan should still be detected"

    def test_check_once_detects_orphan(
        self, mock_tc, monkeypatch, tmp_path,
    ):
        """Broker has SPXW position not in state.json → orphan alert."""
        from webull_bot import reconcile_watchdog as rw

        monkeypatch.setattr(rw, "SEEN_FILE", tmp_path / "seen.json")
        monkeypatch.setattr(rw, "STATUS_FILE", tmp_path / "status.json")
        state_file = tmp_path / "state.json"
        state_file.write_text(json.dumps({"open_position": None}))
        cfg = {"state_file": str(state_file), "account_id": "TEST"}

        import webull_bot.client as client_mod
        monkeypatch.setattr(client_mod, "build_trade_client", lambda: mock_tc)
        # Use a far-future expiry so the active-contract filter passes it through
        mock_tc.holdings = [
            {"instrument_id": "ORPHAN-iid", "symbol": "SPXW",
             "instrument_type": "OPTION", "qty": -1,
             "option_expire_date": "2099-12-31",
             "last_price": "0.75", "market_value": "75.00"},
        ]

        alerts = []
        import webull_bot.alerts as alerts_mod
        monkeypatch.setattr(alerts_mod, "send_alert",
                            lambda text, *a, **kw: alerts.append(text) or True)

        # Force alerts_allowed=True for the test (watchdog gates by ET clock)
        monkeypatch.setattr(rw, "TELEGRAM_ENABLED", True)
        monkeypatch.setattr(rw, "ALERT_OPEN", __import__("datetime").time(0, 0))
        monkeypatch.setattr(rw, "ALERT_CLOSE", __import__("datetime").time(23, 59))

        rw._check_once(cfg, {"orphans": {}, "phantoms": {}})

        status = json.loads((tmp_path / "status.json").read_text())
        assert status["status"] == "orphan"
        assert len(status["orphans"]) == 1
        # Alert only fires on weekdays per the gate; if today is Sat/Sun the
        # alert count will be 0. Either is acceptable — status file is the
        # contract we care about.
        from datetime import datetime as _dt
        from zoneinfo import ZoneInfo as _Z
        if _dt.now(_Z("America/New_York")).weekday() < 5:
            assert len(alerts) == 1
            assert "ORPHAN" in alerts[0]
