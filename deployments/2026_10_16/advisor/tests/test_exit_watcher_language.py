from __future__ import annotations

from advisor import exit_watcher


def test_research_entry_touch_is_not_worded_as_trade_instruction(monkeypatch):
    monkeypatch.setattr(exit_watcher, "_journal_entry", lambda *args: None)
    monkeypatch.setattr(exit_watcher, "is_actionable", lambda view: False)
    view = {"id": "V-1", "instrument": "ABC", "yf_ticker": "ABC",
            "direction": "long", "stop_px": 90.0, "target_px": 120.0,
            "entry_px_low": 99.0, "entry_px_high": 101.0}
    messages = exit_watcher.check_call(view, 100.0, "test", {})
    assert len(messages) == 1
    assert "RESEARCH ENTRY ZONE OBSERVED" in messages[0]
    assert "Not a buy/sell signal" in messages[0]
    assert "BUY ZONE" not in messages[0]


def test_research_stop_is_not_worded_as_exit_order(monkeypatch):
    monkeypatch.setattr(exit_watcher, "_journal_hit", lambda *args: None)
    monkeypatch.setattr(exit_watcher, "is_actionable", lambda view: False)
    view = {"id": "V-1", "instrument": "ABC", "yf_ticker": "ABC",
            "direction": "long", "stop_px": 90.0, "target_px": 120.0}
    message = exit_watcher.check_call(view, 89.0, "test", {})[0]
    assert "RESEARCH VIEW INVALIDATED" in message
    assert "EXIT NOW" not in message
