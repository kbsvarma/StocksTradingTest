from __future__ import annotations

import json

import pandas as pd

from advisor.research import calendar_feed


def test_aggregator_earnings_date_is_never_called_confirmed(tmp_path, monkeypatch):
    research = tmp_path / "research"
    events = research / "events"
    events.mkdir(parents=True)
    pd.DataFrame([{"ticker": "ABC", "next_earnings": "2026-09-04",
                   "earnings_date_spread": 1, "eps_avg": 2.0}]).to_parquet(
                       events / "earnings_calendar.parquet", index=False)
    (research / "candidates_latest.json").write_text(json.dumps(
        {"slate": [{"ticker": "ABC"}]}))
    monkeypatch.setattr(calendar_feed, "RESEARCH_DIR", research)

    class FixedDateTime(calendar_feed.datetime):
        @classmethod
        def now(cls, tz=None):
            return cls.fromisoformat("2026-09-03T08:00:00-04:00")

    monkeypatch.setattr(calendar_feed, "datetime", FixedDateTime)
    result = calendar_feed.build()
    event = result["events"][0]
    assert event["confirmed"] is False
    assert event["date_status"] == "provider_estimate"
    assert event["source_class"] == "secondary_aggregator"
