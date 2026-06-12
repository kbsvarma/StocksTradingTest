"""Vol check — is optionality cheap or rich on this ticker right now?

The single highest-value question from the 2026-06-12 deep dive: the IWM
trade existed because June-30 puts traded at ~26% IV vs 26% realized (zero
event premium into FOMC week) while USO calls at 53% IV had the same
catalysts fully priced. This tool answers that question for any candidate
in one command, so every options idea gets the cheap/rich test BEFORE it
reaches a brief.

Verdict heuristic (ATM IV vs 20d realized):
    < 1.1x  CHEAP  — optionality underpriced vs recent movement
    1.1-1.4 FAIR
    > 1.4x  RICH   — market already charging for the event; prefer shares/spreads

CLI:
    python -m advisor.vol_check --ticker IWM [--max-dte 45]
"""
from __future__ import annotations

import argparse
import math
import sys
import warnings
from datetime import date, datetime

warnings.filterwarnings("ignore")


def check(ticker: str, max_dte: int = 45) -> list[dict]:
    import yfinance as yf

    t = yf.Ticker(ticker)
    c = t.history(period="6mo")["Close"]
    spot = float(c.iloc[-1])
    rv20 = float(c.pct_change().rolling(20).std().iloc[-1]) * math.sqrt(252) * 100
    rv60 = float(c.pct_change().rolling(60).std().iloc[-1]) * math.sqrt(252) * 100
    print(f"{ticker}  spot={spot:.2f}  RV20={rv20:.0f}%  RV60={rv60:.0f}%")
    rows = []
    today = date.today()
    for exp in t.options:
        dte = (datetime.strptime(exp, "%Y-%m-%d").date() - today).days
        if dte < 3 or dte > max_dte:
            continue
        try:
            ch = t.option_chain(exp)
            # ATM = strikes within ±2% of spot only — argsort alone can grab
            # far strikes on thin chains and poison the IV average
            near_p = ch.puts[(ch.puts.strike - spot).abs() <= spot * 0.02]
            near_c = ch.calls[(ch.calls.strike - spot).abs() <= spot * 0.02]
            ivs = list(near_p.impliedVolatility) + list(near_c.impliedVolatility)
            # sanity bounds: junk quotes (IV<3% or >300%) excluded
            ivs = [v for v in ivs if v and 0.03 <= v <= 3.0]
            if len(ivs) < 2:
                continue   # too thin to trust — silence beats a fake verdict
            iv = sum(ivs) / len(ivs) * 100
            ratio = iv / rv20 if rv20 else float("nan")
            verdict = "CHEAP" if ratio < 1.1 else ("FAIR" if ratio <= 1.4 else "RICH")
            rows.append(dict(expiry=exp, dte=dte, atm_iv=round(iv, 0),
                             iv_rv=round(ratio, 2), verdict=verdict))
            print(f"  {exp}  dte={dte:>3}  ATM IV={iv:>4.0f}%  IV/RV20={ratio:.2f}  {verdict}")
        except Exception:
            continue
    if not rows:
        print("  (no usable chains)")
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ticker", required=True)
    ap.add_argument("--max-dte", type=int, default=45)
    a = ap.parse_args()
    check(a.ticker.upper(), a.max_dte)
    return 0


if __name__ == "__main__":
    sys.exit(main())
