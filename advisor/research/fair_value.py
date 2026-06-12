"""Fair-value snapshot for a single name — methods disclosed, confidence labeled.

Three independent estimates, each shown with its inputs (never a black box):

  A. Analyst consensus: mean target ± dispersion (and analyst count — thin
     coverage = wide uncertainty, weight accordingly)
  B. Forward-earnings approach: forward EPS × growth-justified P/E
     (justified P/E = base 8 + 1.5×g capped sanely — a Lynch/PEG-style
     anchor, NOT gospel; shown so the user can disagree with the multiple)
  C. FCF yield reversion: price at which FCF yield would equal the larger
     of 4.5% or 10y+1% — a cash-flow floor estimate for mature names

Verdict: fair-value RANGE (min/max of available methods), upside from spot,
and a confidence grade (A/B/C count + analyst breadth + data completeness).

Free-data caveat (stated, per doctrine): yfinance fundamentals are
point-in-time-now, occasionally stale or missing — every output names which
methods could not run. Paid feeds would sharpen B and C.

CLI: python -m advisor.research.fair_value --ticker NVDA [--json PATH]
"""
from __future__ import annotations

import argparse
import json
import sys
import warnings
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

warnings.filterwarnings("ignore")
ET = ZoneInfo("America/New_York")


def _g(info: dict, k, scale=1.0):
    v = info.get(k)
    try:
        return float(v) * scale if v is not None else None
    except (TypeError, ValueError):
        return None


def estimate(ticker: str) -> dict:
    import yfinance as yf
    t = yf.Ticker(ticker)
    info = t.info or {}
    spot = _g(info, "currentPrice") or _g(info, "regularMarketPrice")
    out = {"ticker": ticker, "as_of": datetime.now(ET).isoformat(),
           "spot": spot, "source": "yfinance fundamentals (point-in-time, free feed)",
           "methods": {}, "missing": []}
    if not spot:
        out["missing"].append("spot price — aborting")
        return out

    m = out["methods"]

    # A — analyst consensus
    tgt, hi, lo = _g(info, "targetMeanPrice"), _g(info, "targetHighPrice"), _g(info, "targetLowPrice")
    n_an = info.get("numberOfAnalystOpinions") or 0
    if tgt:
        m["A_analyst"] = {"fv": round(tgt, 2), "low": lo, "high": hi,
                          "n_analysts": int(n_an),
                          "note": "thin coverage — wide uncertainty" if n_an < 8 else ""}
    else:
        out["missing"].append("A: no analyst targets")

    # B — forward earnings × growth-justified P/E
    fwd_eps = _g(info, "forwardEps")
    growth = _g(info, "earningsGrowth") or _g(info, "revenueGrowth")
    if fwd_eps and fwd_eps > 0:
        g_pct = max(min((growth or 0.08) * 100, 35), 0)   # cap 0-35%
        just_pe = max(8 + 1.5 * g_pct, 8)
        m["B_fwd_earnings"] = {"fv": round(fwd_eps * just_pe, 2),
                               "fwd_eps": fwd_eps, "growth_used_pct": round(g_pct, 1),
                               "justified_pe": round(just_pe, 1),
                               "current_fwd_pe": round(spot / fwd_eps, 1)}
    else:
        out["missing"].append("B: no positive forward EPS")

    # C — FCF yield floor
    fcf = _g(info, "freeCashflow")
    mcap = _g(info, "marketCap")
    if fcf and mcap and fcf > 0:
        try:
            import yfinance as yf2
            tnx = yf2.Ticker("^TNX").history(period="5d")["Close"].iloc[-1]
        except Exception:
            tnx = 4.5
        req = max(4.5, float(tnx) + 1.0) / 100
        fv_mcap = fcf / req
        m["C_fcf_floor"] = {"fv": round(spot * fv_mcap / mcap, 2),
                            "fcf_yield_now_pct": round(fcf / mcap * 100, 2),
                            "required_yield_pct": round(req * 100, 2)}
    else:
        out["missing"].append("C: no positive FCF / mcap")

    fvs = [v["fv"] for v in m.values() if v.get("fv")]
    if fvs:
        out["fair_value_range"] = [round(min(fvs), 2), round(max(fvs), 2)]
        mid = sum(fvs) / len(fvs)
        out["fv_mid"] = round(mid, 2)
        out["upside_to_mid_pct"] = round((mid / spot - 1) * 100, 1)
    grade = "A" if (len(fvs) == 3 and n_an >= 12) else ("B" if len(fvs) >= 2 else "C")
    out["confidence"] = grade
    # quick context
    out["context"] = {k: _g(info, k) for k in
                      ("trailingPE", "forwardPE", "enterpriseToEbitda", "priceToBook",
                       "returnOnEquity", "profitMargins", "revenueGrowth", "earningsGrowth")}
    out["context"]["sector"] = info.get("sector")
    return out


def render(e: dict) -> str:
    L = [f"FAIR VALUE — {e['ticker']}  spot={e.get('spot')}  "
         f"({e['as_of'][:16]})  confidence={e.get('confidence','?')}"]
    for k, v in e.get("methods", {}).items():
        L.append(f"  {k}: fv={v.get('fv')}  " +
                 "  ".join(f"{a}={b}" for a, b in v.items() if a != "fv" and b not in (None, "")))
    if "fair_value_range" in e:
        L.append(f"  RANGE {e['fair_value_range'][0]} – {e['fair_value_range'][1]}"
                 f"   mid {e['fv_mid']}   upside to mid {e['upside_to_mid_pct']:+.1f}%")
    if e.get("missing"):
        L.append(f"  missing: {'; '.join(e['missing'])}")
    L.append(f"  source: {e['source']}")
    return "\n".join(L)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ticker", required=True)
    ap.add_argument("--json")
    a = ap.parse_args()
    e = estimate(a.ticker.upper())
    print(render(e))
    if a.json:
        Path(a.json).parent.mkdir(parents=True, exist_ok=True)
        Path(a.json).write_text(json.dumps(e, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
