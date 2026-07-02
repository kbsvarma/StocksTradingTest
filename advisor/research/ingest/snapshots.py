"""Nightly .info snapshot panel — manufactures point-in-time history.

Yahoo has no history for these fields; we build our own by appending one
observation per ticker per day, stamped with the observation date, NEVER
backfilled (INTELLIGENCE_PLAN §3). Estimate-revision / short-interest /
positioning factors become IC-testable only as this accrues — the clock
starts the first night this runs.

Output: advisor/data/research/snapshots/info/dt=YYYY-MM-DD.parquet
"""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")

# fields verified present in yfinance 1.3.0 .info (2026-07-01 probe)
INFO_FIELDS = [
    "marketCap", "enterpriseValue", "beta", "sharesOutstanding", "floatShares",
    "trailingPE", "forwardPE", "pegRatio", "priceToBook",
    "shortRatio", "sharesShort", "shortPercentOfFloat",
    "heldPercentInstitutions", "heldPercentInsiders",
    "freeCashflow", "operatingCashflow", "totalDebt", "totalCash", "ebitda",
    "returnOnEquity", "grossMargins", "operatingMargins", "profitMargins",
    "earningsGrowth", "revenueGrowth",
    "targetMeanPrice", "targetHighPrice", "targetLowPrice",
    "recommendationKey", "numberOfAnalystOpinions",
    "currentPrice", "fiftyTwoWeekHigh", "fiftyTwoWeekLow", "dividendYield",
]


def _one(ticker: str) -> dict:
    import yfinance as yf
    info = yf.Ticker(ticker).info or {}
    if len(info) < 5:
        # yahoo returns a near-empty dict when throttling — raise so the
        # pool retries after cooldown instead of silently dropping the name
        raise RuntimeError(f"near-empty .info ({len(info)} keys) — throttled?")
    row = {"ticker": ticker}
    for k in INFO_FIELDS:
        row[k] = info.get(k)
    return row


def build(tickers: list[str], out_dir, workers: int = 4) -> dict:
    import pandas as pd
    from advisor.research.ingest._pool import run_pool
    t0 = datetime.now(ET)
    rows, errors, err_samples = run_pool(_one, tickers, workers=workers)
    day = t0.date().isoformat()
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"dt={day}.parquet"
    if rows:
        df = pd.DataFrame(rows)
        df["snapshot_ts"] = t0.isoformat()
        # numeric coercion so parquet types stay stable across nights
        for c in df.columns:
            if c not in ("ticker", "recommendationKey", "snapshot_ts"):
                df[c] = pd.to_numeric(df[c], errors="coerce")
        df.to_parquet(path, index=False)
    return {"rows": len(rows), "errors": errors, "err_samples": err_samples,
            "path": str(path),
            "secs": round((datetime.now(ET) - t0).total_seconds(), 1)}
