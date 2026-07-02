"""peek — compact per-ticker readout of the PIT snapshot stores.

The new-data checklist tool for research sessions (they can't read parquet):
estimate momentum (eps_trend 30d delta, up/down revisions), positioning
(short % float, days-to-cover, inst/insider held), analyst posture, next
earnings + last surprises — everything from OUR OWN dated snapshots, so a
session cites observed data, not a live re-scrape.

CLI: python -m advisor.research.peek --ticker DECK [--json]
"""
from __future__ import annotations

import argparse
import json
import sys

from advisor.research.datastore import RESEARCH_DIR


def _latest(dirpath):
    files = sorted(dirpath.glob("dt=*.parquet"))
    return files[-1] if files else None


def _row(df, ticker):
    hit = df[df.ticker == ticker]
    return hit.iloc[-1].to_dict() if len(hit) else None


def _clean(d: dict | None, keys: list[str]) -> dict | None:
    if d is None:
        return None
    import math
    out = {}
    for k in keys:
        v = d.get(k)
        if isinstance(v, float) and math.isnan(v):
            v = None
        if v is not None:
            out[k] = round(v, 4) if isinstance(v, float) else v
    return out


def peek(ticker: str) -> dict:
    import pandas as pd
    out: dict = {"ticker": ticker, "src": "advisor PIT snapshots (yfinance-derived, nightly)"}

    p = _latest(RESEARCH_DIR / "snapshots" / "info")
    if p:
        row = _row(pd.read_parquet(p), ticker)
        out["snapshot_date"] = p.stem.split("=", 1)[1]
        out["positioning"] = _clean(row, ["shortPercentOfFloat", "shortRatio",
                                          "sharesShort", "heldPercentInstitutions",
                                          "heldPercentInsiders"])
        out["valuation"] = _clean(row, ["marketCap", "forwardPE", "trailingPE",
                                        "pegRatio", "priceToBook", "freeCashflow",
                                        "totalDebt", "ebitda"])
        out["quality"] = _clean(row, ["returnOnEquity", "grossMargins",
                                      "operatingMargins", "earningsGrowth",
                                      "revenueGrowth"])
        out["analyst"] = _clean(row, ["targetMeanPrice", "targetLowPrice",
                                      "targetHighPrice", "recommendationKey",
                                      "numberOfAnalystOpinions"])

    p = _latest(RESEARCH_DIR / "estimates")
    if p:
        row = _row(pd.read_parquet(p), ticker)
        if row:
            em = {}
            for period in ("0q", "0y"):
                cur = row.get(f"epstrend_{period}_current")
                ago = row.get(f"epstrend_{period}_30daysAgo")
                if isinstance(cur, (int, float)) and isinstance(ago, (int, float)) and ago:
                    em[f"eps_{period}_delta30d_pct"] = round((cur / ago - 1) * 100, 2)
            for k in ("epsrev_0q_upLast30days", "epsrev_0q_downLast30days",
                      "epsrev_0y_upLast30days", "epsrev_0y_downLast30days",
                      "rec_0m_strongBuy", "rec_0m_buy", "rec_0m_hold", "rec_0m_sell"):
                v = row.get(k)
                if isinstance(v, (int, float)):
                    em[k] = v
            out["estimate_momentum"] = em or None

    events_dir = RESEARCH_DIR / "events"
    cal = events_dir / "earnings_calendar.parquet"
    if cal.exists():
        row = _row(pd.read_parquet(cal), ticker)
        out["next_earnings"] = _clean(row, ["next_earnings", "earnings_date_spread",
                                            "eps_avg", "eps_low", "eps_high"])
    hist = events_dir / "earnings_history.parquet"
    if hist.exists():
        df = pd.read_parquet(hist)
        rows = df[df.ticker == ticker].sort_values("date").tail(4)
        if len(rows):
            out["last_surprises"] = [
                {"date": r.date, "surprise_pct": None if pd.isna(r.surprise_pct)
                 else round(r.surprise_pct, 1)}
                for r in rows.itertuples()]

    # EDGAR: insider-cluster flag + as-filed fundamentals if cached
    try:
        cl = json.loads((RESEARCH_DIR / "positioning" /
                         "insider_clusters.json").read_text())
        hit = next((c for c in cl.get("clusters", []) if c["ticker"] == ticker), None)
        if hit:
            out["insider_cluster"] = {**hit, "as_of": cl.get("as_of")}
    except Exception:
        pass
    try:
        from advisor.research.edgar import FACTS_DIR, facts_summary
        if (FACTS_DIR / f"{ticker}.parquet").exists():
            fs = facts_summary(ticker)
            if fs:
                keep = ("revenue", "net_income", "eps_diluted", "cfo", "capex",
                        "lt_debt", "cash", "buybacks", "shares_outstanding")
                out["edgar_facts"] = {
                    "src": fs["src"],
                    **{k: v for k, v in fs["concepts"].items() if k in keep}}
    except Exception:
        pass
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ticker", required=True)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    res = peek(a.ticker.upper())
    print(json.dumps(res, indent=None if a.json else 2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
