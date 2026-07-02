"""Nightly ATM-IV snapshot — builds the history that makes IV-RANK honest.

Deliberately coarse (quant-panel cut: Yahoo skew/term-structure is fake
precision from stale mids): per name we store ONLY atm_iv of the nearest
20-45DTE expiry, RV20, the IV/RV ratio, and quote-count. After ~20
observations per name, vol_check can say "IV is in its own Nth percentile"
instead of a naive point-in-time ratio.

Names: CORE liquid ETFs/indices (always) + current candidate-slate equities,
with a min-quote gate — thin small-cap chains are dropped, not averaged.

Output: advisor/data/research/options/iv/dt=YYYY-MM-DD.parquet
"""
from __future__ import annotations

import json
import math
from datetime import date, datetime
from zoneinfo import ZoneInfo

from advisor.research.datastore import RESEARCH_DIR

ET = ZoneInfo("America/New_York")

CORE_IV_NAMES = ["SPY", "QQQ", "IWM", "DIA", "TLT", "HYG", "GLD", "SLV",
                 "USO", "XLE", "XLF", "XLK", "XLV", "XLI", "XLP", "XLU",
                 "XLY", "XLB", "XLRE", "XBI", "SMH", "KRE", "EEM", "FXI"]
MIN_QUOTES = 4
DTE_LO, DTE_HI = 20, 45


def _one(ticker: str) -> dict | None:
    import yfinance as yf
    try:
        t = yf.Ticker(ticker)
        c = t.history(period="4mo")["Close"]
        if len(c) < 25:
            return None
        spot = float(c.iloc[-1])
        rv20 = float(c.pct_change().rolling(20).std().iloc[-1]) * math.sqrt(252) * 100
        today = date.today()
        best = None
        for exp in t.options:
            dte = (datetime.strptime(exp, "%Y-%m-%d").date() - today).days
            if dte < DTE_LO or dte > DTE_HI:
                continue
            ch = t.option_chain(exp)
            near_p = ch.puts[(ch.puts.strike - spot).abs() <= spot * 0.02]
            near_c = ch.calls[(ch.calls.strike - spot).abs() <= spot * 0.02]
            ivs = [v for v in (list(near_p.impliedVolatility)
                               + list(near_c.impliedVolatility))
                   if v and 0.03 <= v <= 3.0]
            if len(ivs) < MIN_QUOTES:
                continue
            best = {"expiry": exp, "dte": dte,
                    "atm_iv": round(sum(ivs) / len(ivs) * 100, 1),
                    "n_quotes": len(ivs)}
            break                      # nearest qualifying expiry only
        if not best:
            return None
        return {"ticker": ticker, "spot": round(spot, 2),
                "rv20": round(rv20, 1),
                "ivrv": round(best["atm_iv"] / rv20, 2) if rv20 else None,
                **best}
    except Exception:
        return None


def build(tickers: list[str], out_dir, workers: int = 4) -> dict:
    """Ingest-runner-compatible signature; `tickers` is ignored in favor of
    CORE + slate (options universe ≠ stock universe)."""
    import pandas as pd
    from concurrent.futures import ThreadPoolExecutor, as_completed
    t0 = datetime.now(ET)
    names = list(CORE_IV_NAMES)
    try:
        slate = json.loads((RESEARCH_DIR / "candidates_latest.json").read_text())
        names += [e["ticker"] for e in slate.get("slate", [])]
    except Exception:
        pass
    names = list(dict.fromkeys(names))
    rows, thin = [], 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = {pool.submit(_one, n): n for n in names}
        for fut in as_completed(futs):
            r = fut.result()
            if r is None:
                thin += 1
            else:
                rows.append(r)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"dt={t0.date().isoformat()}.parquet"
    if rows:
        df = pd.DataFrame(rows)
        df["snapshot_ts"] = t0.isoformat()
        df.to_parquet(path, index=False)
    return {"rows": len(rows), "thin_dropped": thin, "path": str(path),
            "secs": round((datetime.now(ET) - t0).total_seconds(), 1)}


def iv_rank(ticker: str, current_iv: float) -> dict | None:
    """Percentile of current_iv within this ticker's own stored history."""
    import pandas as pd
    vals = []
    for p in sorted((RESEARCH_DIR / "options" / "iv").glob("dt=*.parquet")):
        try:
            df = pd.read_parquet(p, columns=["ticker", "atm_iv"])
            hit = df[df.ticker == ticker]
            if len(hit):
                vals.append(float(hit.atm_iv.iloc[-1]))
        except Exception:
            continue
    if len(vals) < 20:
        return {"n_obs": len(vals), "rank_pct": None,
                "note": f"history accruing ({len(vals)}/20 obs needed)"}
    below = sum(1 for v in vals if v <= current_iv)
    return {"n_obs": len(vals), "rank_pct": round(below / len(vals) * 100)}
