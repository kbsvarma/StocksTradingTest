"""Catalyst calendar — deterministic 14-day lookahead for names we care about.

Sources: events/earnings_calendar.parquet (nightly snapshot) filtered to
held (open journal views) + watchlist + candidate-slate names, tagged by
why we care. Macro prints come from the macro session (macro.json) — this
module is the deterministic equities half.

CLI: python -m advisor.research.calendar_feed [--days 14] [--json PATH]
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from advisor.research.datastore import RESEARCH_DIR

ET = ZoneInfo("America/New_York")


def build(days: int = 14) -> dict:
    import pandas as pd
    today = datetime.now(ET).date()
    horizon = (today + timedelta(days=days)).isoformat()

    care: dict[str, str] = {}          # ticker -> why we care (highest wins)
    try:
        from advisor.research.outcomes import open_views
        for e in open_views():
            t = e.get("yf_ticker") or ""
            if t and not any(x in t for x in ("^", "=F", "=X", "-USD")):
                care[t] = "HELD"
    except Exception:
        pass
    try:
        from advisor.watchlist import load as wl_load
        for t, e in wl_load().items():
            if e.get("state") == "watchlist":
                care.setdefault(t, "watchlist")
    except Exception:
        pass
    try:
        slate = json.loads((RESEARCH_DIR / "candidates_latest.json").read_text())
        for e in slate.get("slate", []):
            care.setdefault(e["ticker"], "slate")
    except Exception:
        pass

    rows = []
    cal_p = RESEARCH_DIR / "events" / "earnings_calendar.parquet"
    if cal_p.exists() and care:
        cal = pd.read_parquet(cal_p)
        for r in cal.itertuples():
            t = r.ticker
            if t not in care:
                continue
            d = str(r.next_earnings)[:10]
            if not (today.isoformat() <= d <= horizon):
                continue
            rows.append({"date": d, "ticker": t, "event": "earnings",
                         "why": care[t],
                         "confirmed": getattr(r, "earnings_date_spread", 1) == 1,
                         "eps_avg": None if pd.isna(getattr(r, "eps_avg", None))
                         else round(float(r.eps_avg), 2)})
    rows.sort(key=lambda x: (x["date"], x["why"] != "HELD"))
    return {"as_of": datetime.now(ET).isoformat(), "horizon_days": days,
            "n_names_tracked": len(care), "events": rows,
            "src": "advisor PIT earnings calendar (yfinance-derived, nightly)"}


def main() -> int:
    args = sys.argv[1:]
    days = int(args[args.index("--days") + 1]) if "--days" in args else 14
    res = build(days)
    if "--json" in args:
        from pathlib import Path
        p = Path(args[args.index("--json") + 1])
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(res, indent=2))
        print(f"[calendar] {len(res['events'])} events → {p}")
    else:
        print(json.dumps(res, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
