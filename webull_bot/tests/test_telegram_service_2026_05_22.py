"""Tests for telegram_service.py internals.

Covers:
  - _build_heartbeat_message in various component states
  - _check_component_health stale detection
  - _should_suppress_by_dedup window logic
  - _maybe_rotate_logs date / hour gating + file moves
  - _send_with_backoff retry behavior

These were verified via live smoke tests during 2026-05-22 deploy but had no
unit coverage. Closes a P4 gap.
"""
from __future__ import annotations

import json
import os
import tempfile
import time
from datetime import datetime, time as dtime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

from webull_bot import telegram_service as ts

ET = ZoneInfo("America/New_York")


def _now_iso(secs_ago: float = 0) -> str:
    return (datetime.now(ET) - timedelta(seconds=secs_ago)).isoformat()


# ─────────────────────────────────────────────────────────────────────────────
# _build_heartbeat_message
# ─────────────────────────────────────────────────────────────────────────────

class TestHeartbeatMessage:
    def _setup(self, tmp_path, monkeypatch, heartbeat=None, state=None,
               watchdog=None, monitor=None, in_market=True):
        """Wire up all the file paths and the market-hours check."""
        # state.json + heartbeat.json sit in the same parent dir per real layout
        state_dir = tmp_path / "vG_spx_w50_vix25"
        state_dir.mkdir()
        state_path = state_dir / "state.json"
        hb_path = state_dir / "heartbeat.json"
        if state is not None:
            state_path.write_text(json.dumps(state))
        if heartbeat is not None:
            hb_path.write_text(json.dumps(heartbeat))
        # mock the /tmp files
        wd_path = tmp_path / "wd.json"
        mon_path = tmp_path / "mon.json"
        if watchdog is not None:
            wd_path.write_text(json.dumps(watchdog))
        if monitor is not None:
            mon_path.write_text(json.dumps(monitor))

        # Patch module-level state path
        monkeypatch.setattr(ts, "STATE_PATH", str(state_path))
        # _read_watchdog_status reads hardcoded /tmp path — patch the open
        monkeypatch.setattr(ts, "_read_watchdog_status", lambda: watchdog or {})
        monkeypatch.setattr(ts, "_read_monitor_tick", lambda: monitor or {})
        monkeypatch.setattr(ts, "_is_market_hours", lambda: in_market)

    def test_all_green_no_position(self, tmp_path, monkeypatch):
        self._setup(
            tmp_path, monkeypatch,
            heartbeat={"ts": _now_iso(5)},
            state={"open_position": None, "total_trades": 0},
            watchdog={"ts": _now_iso(20), "status": "ok"},
        )
        msg = ts._build_heartbeat_message()
        assert msg.startswith("🟢")
        assert "alive" in msg
        assert "Position: none" in msg

    def test_bot_stale_shows_red(self, tmp_path, monkeypatch):
        self._setup(
            tmp_path, monkeypatch,
            heartbeat={"ts": _now_iso(500)},  # >420s threshold
            state={"open_position": None},
            watchdog={"ts": _now_iso(20), "status": "ok"},
            in_market=True,
        )
        msg = ts._build_heartbeat_message()
        assert msg.startswith("🔴")
        assert "STALE" in msg

    def test_outside_market_hours_no_false_stale(self, tmp_path, monkeypatch):
        """Outside market hours, stale watchdog/bot is NORMAL — show green."""
        self._setup(
            tmp_path, monkeypatch,
            heartbeat={"ts": _now_iso(10000)},  # very stale
            state={"open_position": None},
            watchdog={"ts": _now_iso(10000), "status": "ok"},
            in_market=False,
        )
        msg = ts._build_heartbeat_message()
        assert msg.startswith("🟢")

    def test_with_open_position_shows_strikes_and_upl(self, tmp_path, monkeypatch):
        self._setup(
            tmp_path, monkeypatch,
            heartbeat={"ts": _now_iso(5)},
            state={
                "open_position": {
                    "short_strike": 7420, "long_strike": 7370,
                    "entry_credit": 2.0, "stop_price": 4.0, "quantity": 1,
                },
            },
            watchdog={"ts": _now_iso(20), "status": "ok"},
            monitor={"ts": _now_iso(2), "mark": 1.30},
        )
        msg = ts._build_heartbeat_message()
        assert "7420/7370P" in msg
        assert "credit=$2.0" in msg
        assert "stop=$4.0" in msg
        assert "mark=$1.3" in msg
        assert "UPL=$+70" in msg  # UPL = (2.0-1.3)*100*1, formatted as $+70

    def test_today_pnl_included_when_traded(self, tmp_path, monkeypatch):
        self._setup(
            tmp_path, monkeypatch,
            heartbeat={"ts": _now_iso(5)},
            state={
                "open_position": None,
                "trade_taken_today": True,
                "total_trades": 17, "wins": 16, "losses": 1, "total_pnl": 730,
            },
            watchdog={"ts": _now_iso(20), "status": "ok"},
        )
        msg = ts._build_heartbeat_message()
        assert "traded ✓" in msg
        assert "17t" in msg
        assert "16W/1L" in msg
        assert "94%" in msg
        assert "+730" in msg

    def test_inf_age_renders_na(self, tmp_path, monkeypatch):
        self._setup(
            tmp_path, monkeypatch,
            heartbeat=None,  # no file → age = inf
            state={"open_position": None},
            watchdog=None,
            in_market=False,
        )
        msg = ts._build_heartbeat_message()
        # Should not crash and should show n/a for ages
        assert "n/a" in msg


# ─────────────────────────────────────────────────────────────────────────────
# _check_component_health
# ─────────────────────────────────────────────────────────────────────────────

class TestComponentHealth:
    def _setup(self, monkeypatch, hb=None, wd=None, mon=None, state=None, in_market=True):
        monkeypatch.setattr(ts, "_read_heartbeat", lambda: hb or {})
        monkeypatch.setattr(ts, "_read_watchdog_status", lambda: wd or {})
        monkeypatch.setattr(ts, "_read_monitor_tick", lambda: mon or {})
        monkeypatch.setattr(ts, "_read_state_json", lambda: state or {})
        monkeypatch.setattr(ts, "_is_market_hours", lambda: in_market)

    def test_no_alerts_when_all_fresh(self, monkeypatch):
        self._setup(monkeypatch,
                    hb={"ts": _now_iso(10)},
                    wd={"ts": _now_iso(30)},
                    state={"open_position": None})
        assert ts._check_component_health() == []

    def test_alerts_bot_stale_during_market(self, monkeypatch):
        self._setup(monkeypatch,
                    hb={"ts": _now_iso(500)},  # >420s
                    wd={"ts": _now_iso(30)},
                    state={"open_position": None})
        alerts = ts._check_component_health()
        assert any("BOT MAIN LOOP" in a["subject"] for a in alerts)

    def test_alerts_watchdog_stale(self, monkeypatch):
        self._setup(monkeypatch,
                    hb={"ts": _now_iso(10)},
                    wd={"ts": _now_iso(400)},  # >300s
                    state={"open_position": None})
        alerts = ts._check_component_health()
        assert any("WATCHDOG" in a["subject"] for a in alerts)

    def test_alerts_monitor_stale_only_with_open_position(self, monkeypatch):
        self._setup(monkeypatch,
                    hb={"ts": _now_iso(10)},
                    wd={"ts": _now_iso(30)},
                    mon={"ts": _now_iso(60)},  # >30s
                    state={"open_position": {"short_strike": 7420}})
        alerts = ts._check_component_health()
        assert any("SL MONITOR" in a["subject"] for a in alerts)

    def test_no_monitor_alert_when_no_position(self, monkeypatch):
        self._setup(monkeypatch,
                    hb={"ts": _now_iso(10)},
                    wd={"ts": _now_iso(30)},
                    mon={"ts": _now_iso(99999)},  # very stale
                    state={"open_position": None})
        alerts = ts._check_component_health()
        assert not any("MONITOR" in a["subject"] for a in alerts)

    def test_skip_outside_market_hours(self, monkeypatch):
        self._setup(monkeypatch,
                    hb={"ts": _now_iso(99999)},
                    wd={"ts": _now_iso(99999)},
                    state={"open_position": None},
                    in_market=False)
        assert ts._check_component_health() == []


# ─────────────────────────────────────────────────────────────────────────────
# Dedup window
# ─────────────────────────────────────────────────────────────────────────────

class TestDedup:
    def test_no_dedup_key_never_suppresses(self):
        assert ts._should_suppress_by_dedup("", {}) is False

    def test_no_prior_entry_does_not_suppress(self):
        assert ts._should_suppress_by_dedup("orphan:abc", {}) is False

    def test_recent_entry_within_window_suppresses(self):
        dedup = {"orphan:abc": _now_iso(60)}  # 1 min ago, window is 30 min
        assert ts._should_suppress_by_dedup("orphan:abc", dedup) is True

    def test_old_entry_past_window_does_not_suppress(self):
        dedup = {"orphan:abc": _now_iso(2000)}  # 33 min ago, past 30-min window
        assert ts._should_suppress_by_dedup("orphan:abc", dedup) is False

    def test_unparseable_ts_does_not_suppress(self):
        dedup = {"orphan:abc": "not-a-timestamp"}
        assert ts._should_suppress_by_dedup("orphan:abc", dedup) is False


# ─────────────────────────────────────────────────────────────────────────────
# Log rotation
# ─────────────────────────────────────────────────────────────────────────────

class TestLogRotation:
    def _setup_files(self, tmp_path, monkeypatch, with_content=True):
        """Create fake queue/sent/handled files and patch paths."""
        eq_file = tmp_path / "event_queue.jsonl"
        sent_file = tmp_path / "telegram_sent.log"
        handled_file = tmp_path / "bot_queue_handled.log"
        archive_dir = tmp_path / "archive"
        if with_content:
            eq_file.write_text('{"id":"a","subject":"test"}\n')
            sent_file.write_text('{"id":"a","http_status":200}\n')
            handled_file.write_text("a|test|2026-05-22\n")
        monkeypatch.setattr(ts, "EVENT_QUEUE", eq_file)
        monkeypatch.setattr(ts, "SENT_LOG", sent_file)
        monkeypatch.setattr(ts, "HANDLED_LOG", handled_file)
        monkeypatch.setattr(ts, "ROTATE_DIR", archive_dir)
        # reset module-level rotation memo
        monkeypatch.setattr(ts, "_last_rotation_date", "")
        return eq_file, sent_file, handled_file, archive_dir

    def test_skips_before_rotate_hour(self, tmp_path, monkeypatch):
        eq, sent, handled, archive = self._setup_files(tmp_path, monkeypatch)
        # Patch datetime.now to return a time before 23:00
        fake_now = datetime(2026, 5, 22, 14, 0, tzinfo=ET)
        with patch.object(ts, "datetime") as mock_dt:
            mock_dt.now.return_value = fake_now
            ts._maybe_rotate_logs()
        # Files still in place, no archive
        assert eq.exists()
        assert sent.exists()
        assert handled.exists()
        assert not archive.exists()

    def test_rotates_after_rotate_hour(self, tmp_path, monkeypatch):
        eq, sent, handled, archive = self._setup_files(tmp_path, monkeypatch)
        fake_now = datetime(2026, 5, 22, 23, 30, tzinfo=ET)
        with patch.object(ts, "datetime") as mock_dt:
            mock_dt.now.return_value = fake_now
            ts._maybe_rotate_logs()
        # Originals now empty (touched)
        assert eq.stat().st_size == 0
        assert sent.stat().st_size == 0
        # Archives exist
        assert archive.exists()
        archived = list(archive.glob("*.archive"))
        assert len(archived) >= 2  # eq + sent (handled may not have content)

    def test_idempotent_same_day(self, tmp_path, monkeypatch):
        eq, sent, handled, archive = self._setup_files(tmp_path, monkeypatch)
        fake_now = datetime(2026, 5, 22, 23, 30, tzinfo=ET)
        with patch.object(ts, "datetime") as mock_dt:
            mock_dt.now.return_value = fake_now
            ts._maybe_rotate_logs()
            n_after_first = len(list(archive.glob("*.archive")))
            # Re-write content and try to rotate again same day
            eq.write_text('{"id":"b"}\n')
            ts._maybe_rotate_logs()
            n_after_second = len(list(archive.glob("*.archive")))
        assert n_after_first == n_after_second  # idempotent — no double-rotate

    def test_skips_empty_files(self, tmp_path, monkeypatch):
        eq, sent, handled, archive = self._setup_files(tmp_path, monkeypatch, with_content=False)
        # Touch all files but leave empty
        eq.touch(); sent.touch(); handled.touch()
        fake_now = datetime(2026, 5, 22, 23, 30, tzinfo=ET)
        with patch.object(ts, "datetime") as mock_dt:
            mock_dt.now.return_value = fake_now
            ts._maybe_rotate_logs()
        # No archives created (nothing to archive)
        if archive.exists():
            assert len(list(archive.glob("*.archive"))) == 0


# ─────────────────────────────────────────────────────────────────────────────
# _send_with_backoff
# ─────────────────────────────────────────────────────────────────────────────

class TestSendWithBackoff:
    def test_succeeds_first_try(self, monkeypatch, tmp_path):
        monkeypatch.setattr(ts, "SENT_LOG", tmp_path / "sent.log")
        monkeypatch.setattr(ts, "_send_telegram", lambda t, parse_mode="": (200, "ok", ""))
        ev = {"id": "abc", "subject": "test", "body": "", "severity": "info"}
        with patch("time.sleep"):
            result = ts._send_with_backoff(ev)
        assert result is True

    def test_retries_then_succeeds(self, monkeypatch, tmp_path):
        monkeypatch.setattr(ts, "SENT_LOG", tmp_path / "sent.log")
        calls = [0]
        def mock_send(text, parse_mode=""):
            calls[0] += 1
            if calls[0] < 3:
                return (500, "", "server error")
            return (200, "ok", "")
        monkeypatch.setattr(ts, "_send_telegram", mock_send)
        ev = {"id": "retry1", "subject": "test", "body": "", "severity": "info"}
        with patch("time.sleep"):
            result = ts._send_with_backoff(ev)
        assert result is True
        assert calls[0] == 3

    def test_fails_after_exhausting_retries(self, monkeypatch, tmp_path):
        monkeypatch.setattr(ts, "SENT_LOG", tmp_path / "sent.log")
        monkeypatch.setattr(ts, "_send_telegram", lambda t, parse_mode="": (500, "", "server"))
        ev = {"id": "fail1", "subject": "test", "body": "", "severity": "info"}
        with patch("time.sleep"):
            result = ts._send_with_backoff(ev)
        assert result is False

    def test_no_retry_on_missing_credentials(self, monkeypatch, tmp_path):
        monkeypatch.setattr(ts, "SENT_LOG", tmp_path / "sent.log")
        calls = [0]
        def mock_send(text, parse_mode=""):
            calls[0] += 1
            return (0, "", "credentials missing")
        monkeypatch.setattr(ts, "_send_telegram", mock_send)
        ev = {"id": "nocred", "subject": "test", "body": "", "severity": "info"}
        with patch("time.sleep"):
            result = ts._send_with_backoff(ev)
        assert result is False
        assert calls[0] == 1  # didn't retry

    def test_severity_emoji_prefix_in_text(self, monkeypatch, tmp_path):
        monkeypatch.setattr(ts, "SENT_LOG", tmp_path / "sent.log")
        sent_text = {"text": None}
        def capture(text, parse_mode=""):
            sent_text["text"] = text
            return (200, "ok", "")
        monkeypatch.setattr(ts, "_send_telegram", capture)
        ev = {"id": "x", "subject": "ORPHAN", "body": "details", "severity": "critical"}
        with patch("time.sleep"):
            ts._send_with_backoff(ev)
        assert "🚨" in sent_text["text"]
        assert "ORPHAN" in sent_text["text"]
        assert "details" in sent_text["text"]

    def test_logs_attempt_to_sent_log(self, monkeypatch, tmp_path):
        sent_log = tmp_path / "sent.log"
        monkeypatch.setattr(ts, "SENT_LOG", sent_log)
        monkeypatch.setattr(ts, "_send_telegram", lambda t, parse_mode="": (200, "{ok:true}", ""))
        ev = {"id": "loggable", "subject": "x", "body": "", "severity": "info"}
        with patch("time.sleep"):
            ts._send_with_backoff(ev)
        assert sent_log.exists()
        line = sent_log.read_text().strip()
        rec = json.loads(line)
        assert rec["id"] == "loggable"
        assert rec["http_status"] == 200


# ─────────────────────────────────────────────────────────────────────────────
# Monitor combo-based alive check
# ─────────────────────────────────────────────────────────────────────────────

class TestMonitorAliveCheck:
    def _make_monitor(self, broker_response, raises=False):
        """Create a SpreadMonitor with mocked execution.trade.account_v2."""
        from webull_bot.monitor import PositionMonitor
        tc = MagicMock()
        if raises:
            tc.account_v2.get_account_position.side_effect = Exception("API down")
        else:
            resp = MagicMock()
            resp.status_code = 200
            resp.json.return_value = broker_response
            tc.account_v2.get_account_position.return_value = resp
        execution = MagicMock()
        execution.trade = tc
        execution.account_id = "TEST"
        # Build the monitor (need a real instance — just instantiate via MagicMock for simplicity)
        m = MagicMock()
        m.execution = execution
        # Bind the real method
        m._position_still_at_broker = PositionMonitor._position_still_at_broker.__get__(m)
        return m

    def _make_open_position(self, short_iid="POS123", short_strike=7420):
        from webull_bot.state import OpenPosition
        return OpenPosition(
            symbol="SPXW", expiry="2026-05-22",
            short_strike=short_strike, long_strike=short_strike-50, quantity=1,
            entry_credit=2.0, stop_price=4.0,
            entry_spx=7480.0, entry_vix=17.0,
            entry_ts="2026-05-22T10:30:00",
            client_order_id="X",
            short_iid=short_iid, long_iid=short_iid,
        )

    def _make_combo(self, position_id="POS123", short_strike=7420, long_strike=7370):
        return {
            "symbol": "SPXW",
            "option_strategy": "VERTICAL",
            "position_id": position_id,
            "legs": [
                {"option_exercise_price": str(long_strike)},
                {"option_exercise_price": str(short_strike)},
            ],
        }

    def test_finds_position_by_id(self):
        combo = self._make_combo(position_id="POS123", short_strike=7420)
        m = self._make_monitor([combo])
        pos = self._make_open_position(short_iid="POS123", short_strike=7420)
        from webull_bot.safe_api import safe_call
        with patch("webull_bot.safe_api.safe_call", return_value=m.execution.trade.account_v2.get_account_position.return_value):
            with patch("time.sleep"):
                assert m._position_still_at_broker(pos) is True

    def test_returns_false_when_combo_gone(self):
        # Broker has different position only
        other = self._make_combo(position_id="OTHER", short_strike=7300)
        m = self._make_monitor([other])
        pos = self._make_open_position(short_iid="POS123", short_strike=7420)
        with patch("webull_bot.safe_api.safe_call", return_value=m.execution.trade.account_v2.get_account_position.return_value):
            with patch("time.sleep"):
                assert m._position_still_at_broker(pos) is False

    def test_matches_by_strike_when_position_id_differs(self):
        # Broker has same strikes but different position_id (edge case)
        combo = self._make_combo(position_id="DIFFERENT_ID", short_strike=7420)
        m = self._make_monitor([combo])
        pos = self._make_open_position(short_iid="POS123", short_strike=7420)
        with patch("webull_bot.safe_api.safe_call", return_value=m.execution.trade.account_v2.get_account_position.return_value):
            with patch("time.sleep"):
                assert m._position_still_at_broker(pos) is True

    def test_returns_true_on_api_error(self):
        """On any exception, assume still open — over-retry SL > miss it."""
        m = self._make_monitor(None, raises=True)
        pos = self._make_open_position()
        with patch("webull_bot.safe_api.safe_call", side_effect=Exception("down")):
            with patch("time.sleep"):
                assert m._position_still_at_broker(pos) is True

    def test_returns_true_on_safe_call_none(self):
        """safe_call returns None when all retries exhausted → assume still open."""
        m = self._make_monitor([])
        pos = self._make_open_position()
        with patch("webull_bot.safe_api.safe_call", return_value=None):
            with patch("time.sleep"):
                assert m._position_still_at_broker(pos) is True
