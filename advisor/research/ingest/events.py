"""Earnings calendar + surprise history.

calendar → earnings_calendar.parquet (full rewrite nightly, as_of column):
next confirmed earnings date + consensus per ticker — feeds the morning
session's earnings-in-N-days awareness and (later) the kill-test checklist.

earnings_dates → earnings_history.parquet (merged, deduped on ticker+date):
past EPS estimate/actual/surprise — the substrate for SUE/PEAD once enough
of our own point-in-time consensus snapshots accrue. NOTE (quant-skeptic
2026-07-01): Yahoo's surprise history uses CURRENT consensus, not the
consensus at the print — fine as a candidate flag, NOT valid for PEAD
backtests. Backtests must use our own estimates/dt= snapshots.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")


def _one(ticker: str) -> tuple[dict | None, list[dict]]:
    import pandas as pd
    import yfinance as yf
    cal_row, hist_rows = None, []
    try:
        tk = yf.Ticker(ticker)
        try:
            cal = tk.calendar or {}
            dates = cal.get("Earnings Date") or []
            if dates:
                cal_row = {
                    "ticker": ticker,
                    "next_earnings": str(dates[0]),
                    "earnings_date_spread": len(dates),   # >1 = unconfirmed window
                    "eps_avg": cal.get("Earnings Average"),
                    "eps_low": cal.get("Earnings Low"),
                    "eps_high": cal.get("Earnings High"),
                    "rev_avg": cal.get("Revenue Average"),
                    "ex_div": str(cal.get("Ex-Dividend Date") or ""),
                }
        except Exception:
            pass
        try:
            ed = tk.earnings_dates
            if ed is not None and not ed.empty:
                for ts, r in ed.iterrows():
                    est = r.get("EPS Estimate")
                    act = r.get("Reported EPS")
                    if not isinstance(act, (int, float)) or pd.isna(act):
                        continue          # future/unreported rows
                    sur = r.get("Surprise(%)")
                    hist_rows.append({
                        "ticker": ticker, "date": ts.date().isoformat(),
                        "eps_estimate": float(est) if isinstance(est, (int, float)) and pd.notna(est) else None,
                        "eps_actual": float(act),
                        "surprise_pct": float(sur) if isinstance(sur, (int, float)) and pd.notna(sur) else None,
                    })
        except Exception:
            pass
    except Exception:
        pass
    return cal_row, hist_rows


def build(tickers: list[str], out_dir, workers: int = 6) -> dict:
    import pandas as pd
    t0 = datetime.now(ET)
    cal_rows, hist_rows, errors = [], [], 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = {pool.submit(_one, t): t for t in tickers}
        for fut in as_completed(futs):
            cal, hist = fut.result()
            if cal is None and not hist:
                errors += 1
            if cal:
                cal_rows.append(cal)
            hist_rows.extend(hist)
    out_dir.mkdir(parents=True, exist_ok=True)

    cal_path = out_dir / "earnings_calendar.parquet"
    if cal_rows:
        cal_df = pd.DataFrame(cal_rows)
        cal_df["as_of"] = t0.isoformat()
        cal_df.to_parquet(cal_path, index=False)

    hist_path = out_dir / "earnings_history.parquet"
    if hist_rows:
        new = pd.DataFrame(hist_rows)
        new["first_observed"] = t0.date().isoformat()
        if hist_path.exists():
            old = pd.read_parquet(hist_path)
            merged = pd.concat([old, new], ignore_index=True)
            # keep the FIRST observation of each print (PIT discipline)
            merged = merged.sort_values("first_observed").drop_duplicates(
                subset=["ticker", "date"], keep="first")
        else:
            merged = new.drop_duplicates(subset=["ticker", "date"], keep="first")
        merged.to_parquet(hist_path, index=False)

    return {"rows": len(cal_rows), "history_rows": len(hist_rows),
            "errors": errors, "path": str(cal_path),
            "secs": round((datetime.now(ET) - t0).total_seconds(), 1)}
