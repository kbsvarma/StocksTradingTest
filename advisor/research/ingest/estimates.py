"""Nightly analyst-estimate snapshot — the estimate-momentum goldmine.

Flattens yfinance eps_trend / eps_revisions / earnings_estimate /
revenue_estimate / recommendations into one wide row per ticker per day.
Same append-only, never-backfilled discipline as snapshots.py: the
est_revision factor is prospective-only until this history accrues.

Output: advisor/data/research/estimates/dt=YYYY-MM-DD.parquet
Columns like: epstrend_0q_current, epstrend_0q_30daysAgo,
epsrev_0q_upLast30days, epsest_0q_avg, revest_0y_growth, rec_0m_strongBuy…
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")


def _flatten(df, prefix: str, row: dict) -> None:
    """DataFrame indexed by period (0q,+1q,0y,+1y) → flat numeric columns."""
    if df is None or getattr(df, "empty", True):
        return
    for period, series in df.iterrows():
        p = str(period).replace("+", "p")
        for col, val in series.items():
            if isinstance(val, (int, float)):
                row[f"{prefix}_{p}_{col}"] = val


def _one(ticker: str) -> dict | None:
    import yfinance as yf
    try:
        tk = yf.Ticker(ticker)
        row: dict = {"ticker": ticker}
        for attr, prefix in (("eps_trend", "epstrend"),
                             ("eps_revisions", "epsrev"),
                             ("earnings_estimate", "epsest"),
                             ("revenue_estimate", "revest")):
            try:
                _flatten(getattr(tk, attr), prefix, row)
            except Exception:
                continue
        try:
            rec = tk.recommendations
            if rec is not None and not rec.empty:
                for _, r in rec.iterrows():
                    p = str(r.get("period", "?")).replace("-", "m")
                    if p not in ("0m", "m1m"):
                        continue
                    for col in ("strongBuy", "buy", "hold", "sell", "strongSell"):
                        if col in r:
                            row[f"rec_{p}_{col}"] = r[col]
        except Exception:
            pass
        return row if len(row) > 1 else None   # ticker-only row = nothing worked
    except Exception:
        return None


def build(tickers: list[str], out_dir, workers: int = 6) -> dict:
    import pandas as pd
    t0 = datetime.now(ET)
    rows, errors = [], 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = {pool.submit(_one, t): t for t in tickers}
        for fut in as_completed(futs):
            row = fut.result()
            if row is None:
                errors += 1
            else:
                rows.append(row)
    day = t0.date().isoformat()
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"dt={day}.parquet"
    if rows:
        df = pd.DataFrame(rows)
        df["snapshot_ts"] = t0.isoformat()
        df.to_parquet(path, index=False)
    return {"rows": len(rows), "errors": errors, "path": str(path),
            "secs": round((datetime.now(ET) - t0).total_seconds(), 1)}
