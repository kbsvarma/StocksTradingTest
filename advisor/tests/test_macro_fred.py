from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from advisor.research.ingest.macro_fred import SERIES, build


def test_fred_snapshot_has_provenance_freshness_and_curve():
    def fetcher(series_id):
        value = {"DGS2": 4.1, "DGS10": 4.5, "T10YIE": 2.3,
                 "BAMLH0A0HYM2": 3.2, "NFCI": -0.4}[series_id]
        return f"observation_date,{series_id}\n2026-09-01,{value}\n"
    result = build(now=datetime(2026, 9, 3, 12,
                                tzinfo=ZoneInfo("America/New_York")), fetcher=fetcher)
    assert result["ok"] is True
    assert result["fresh_series"] == len(SERIES)
    assert result["observations"]["CURVE_2S10S"]["value"] == .4
    assert result["observations"]["DGS10"]["source"].startswith("https://fred")


def test_fred_snapshot_fails_closed_when_critical_series_missing():
    def fetcher(series_id):
        if series_id == "DGS10":
            raise TimeoutError("provider down")
        return f"observation_date,{series_id}\n2026-09-01,1.0\n"
    result = build(now=datetime(2026, 9, 3,
                                tzinfo=ZoneInfo("America/New_York")), fetcher=fetcher)
    assert result["ok"] is False
    assert "DGS10" in result["errors"]
