"""CME session availability used by live ingestion and nightly replay."""
from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo


ET = ZoneInfo("America/New_York")

# Equity-index and metals schedule. CME finalizes product-specific holiday
# hours close to each holiday; the final-host runbook requires re-verification.
CME_FULL_CLOSE = {
    "2026-01-01", "2026-04-03", "2026-12-25",
    "2027-01-01", "2027-03-26", "2027-12-24",
}
CME_EARLY_CLOSE = {
    "2026-01-19", "2026-02-16", "2026-05-25", "2026-06-19",
    "2026-07-03", "2026-09-07", "2026-11-26", "2026-11-27",
    "2026-12-24", "2027-01-18", "2027-02-15", "2027-05-31",
    "2027-06-18", "2027-07-05", "2027-09-06", "2027-11-25",
    "2027-11-26",
}


def is_market_open(now_utc: datetime) -> bool:
    """Conservative Globex availability: Sun 18:00–Fri 17:00 ET, daily
    maintenance break, minus the release holiday table above."""
    et = now_utc.astimezone(ET)
    wd, h = et.weekday(), et.hour
    if wd == 5:
        return False
    if wd == 6 and h < 18:
        return False
    if wd == 4 and h >= 17:
        return False
    if h == 17:
        return False
    session_date = (et.date() + timedelta(days=1)) if h >= 18 else et.date()
    if session_date.isoformat() in CME_FULL_CLOSE:
        return False
    if et.date().isoformat() in CME_EARLY_CLOSE and 12 <= h < 18:
        return False
    return True
