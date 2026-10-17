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

2026-07-02 upgrades (both audit blind spots fixed):
  IV-RANK    — verdict now also reports where today's ATM IV sits within
               this ticker's OWN accrued nightly history (options/iv/
               snapshots; needs ~20 obs, honest "accruing" note until then)
  EARNINGS   — an earnings date inside the DTE window is flagged: rich IV
               may be JUSTIFIED there; defined-risk only across the print
  PERSISTED  — every run appends to research/options/vol_checks.jsonl so
               verdicts are scoreable later and dossiers can cite them

CLI:
    python -m advisor.vol_check --ticker IWM [--max-dte 45]
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import warnings
from datetime import date, datetime
from zoneinfo import ZoneInfo

warnings.filterwarnings("ignore")
ET = ZoneInfo("America/New_York")


def _next_earnings(ticker: str) -> str | None:
    try:
        import pandas as pd
        from advisor.research.datastore import RESEARCH_DIR
        cal = pd.read_parquet(RESEARCH_DIR / "events" / "earnings_calendar.parquet")
        hit = cal[cal.ticker == ticker]
        return str(hit.next_earnings.iloc[-1])[:10] if len(hit) else None
    except Exception:
        return None


def check(ticker: str, max_dte: int = 45) -> list[dict]:
    import yfinance as yf

    t = yf.Ticker(ticker)
    c = t.history(period="6mo")["Close"]
    spot = float(c.iloc[-1])
    rv20 = float(c.pct_change().rolling(20).std().iloc[-1]) * math.sqrt(252) * 100
    rv60 = float(c.pct_change().rolling(60).std().iloc[-1]) * math.sqrt(252) * 100
    print(f"{ticker}  spot={spot:.2f}  RV20={rv20:.0f}%  RV60={rv60:.0f}%")
    earnings = _next_earnings(ticker)
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
            row = dict(expiry=exp, dte=dte, atm_iv=round(iv, 0),
                       iv_rv=round(ratio, 2), verdict=verdict)
            flag = ""
            if earnings and str(today.isoformat()) <= earnings <= exp:
                row["earnings_in_window"] = earnings
                flag = (f"  ⚠ EARNINGS {earnings} INSIDE WINDOW — rich IV may "
                        f"be justified; defined-risk only across the print")
            rows.append(row)
            print(f"  {exp}  dte={dte:>3}  ATM IV={iv:>4.0f}%  IV/RV20={ratio:.2f}  {verdict}{flag}")
        except Exception:
            continue
    if not rows:
        print("  (no usable chains)")
        return rows

    # IV-rank vs this ticker's own accrued nightly history
    try:
        from advisor.research.ingest.iv_surface import iv_rank
        rank = iv_rank(ticker, rows[0]["atm_iv"])
        if rank:
            if rank.get("rank_pct") is not None:
                print(f"  IV-RANK: today's ATM IV is in the {rank['rank_pct']}th "
                      f"percentile of its own history ({rank['n_obs']} obs)")
            else:
                print(f"  IV-RANK: {rank['note']}")
            rows[0]["iv_rank"] = rank
    except Exception:
        pass

    # persist — verdicts are scoreable and citable, not stdout-and-gone
    try:
        from advisor.research.datastore import RESEARCH_DIR
        out = RESEARCH_DIR / "options" / "vol_checks.jsonl"
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": datetime.now(ET).isoformat(),
                                "ticker": ticker, "spot": round(spot, 2),
                                "rv20": round(rv20, 1), "rv60": round(rv60, 1),
                                "earnings": earnings, "rows": rows}) + "\n")
    except Exception:
        pass
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
