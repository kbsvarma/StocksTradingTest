"""Quant pass — deterministic technical/cross-asset computation for every brief.

Productized from the 2026-06-12 deep-dive session: every advisor session gets
this table for free instead of eyeballing charts. Computed, not vibed.

Per ticker: trend structure (vs 20/50/200dma), RSI(14), ATR%, 20d realized
vol, 5/21/63d returns, drawdown from 52w high, auto-flags (EXTENDED /
CAPITULATION / BREAKOUT / BREAKDOWN).

Derived cross-asset: breadth (RSP/SPY), small-cap rate sensitivity (IWM~TLT
corr), gold real-rate link (GLD~TNX corr), credit stress (HYG), VIX term
structure, oil percentile.

CLI:
    python -m advisor.quant                 # human table
    python -m advisor.quant --json PATH
"""
from __future__ import annotations

import json
import math
import sys
import warnings
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

warnings.filterwarnings("ignore")
ET = ZoneInfo("America/New_York")

TKRS = ["SPY", "QQQ", "IWM", "RSP", "SMH", "XLE", "XLF", "XLK", "XLV", "XLI",
        "XLB", "XLY", "XLP", "XLU", "XLRE", "XLC", "GLD", "SLV", "USO", "UNG",
        "TLT", "IEF", "HYG", "UUP", "BTC-USD", "^VIX", "^VIX3M", "^TNX"]


def _rsi(s, n=14):
    d = s.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n).mean()
    return 100 - 100 / (1 + up / dn)


def _flags(r: dict) -> str:
    f = []
    if r["vs20"] > 4 and r["rsi"] >= 65:
        f.append("EXTENDED")
    if r["rsi"] <= 35 and r["dd52"] <= -15:
        f.append("CAPITULATION")
    if r["dd52"] >= -0.5 and r["r5"] > 1.5:
        f.append("AT-HIGHS")
    if r["vs200"] < 0 and r["vs50"] < 0 and r["r21"] < -5:
        f.append("BREAKDOWN")
    return ",".join(f)


def build() -> dict:
    import pandas as pd
    import yfinance as yf

    data = yf.download(TKRS, period="2y", interval="1d", auto_adjust=True,
                       progress=False, group_by="ticker", threads=True)
    out = {"as_of": datetime.now(ET).isoformat(),
           "source": "yfinance daily (EOD/delayed) — computed, not quoted",
           "rows": [], "derived": {}}
    closes = {}
    for t in TKRS:
        try:
            df = data[t].dropna()
            c, h, l = df["Close"], df["High"], df["Low"]
            if len(c) < 220:
                continue
            closes[t] = c
            last = float(c.iloc[-1])
            tr = pd.concat([h - l, (h - c.shift()).abs(),
                            (l - c.shift()).abs()], axis=1).max(axis=1)
            r = dict(
                ticker=t, px=round(last, 2),
                vs20=round((last / c.rolling(20).mean().iloc[-1] - 1) * 100, 1),
                vs50=round((last / c.rolling(50).mean().iloc[-1] - 1) * 100, 1),
                vs200=round((last / c.rolling(200).mean().iloc[-1] - 1) * 100, 1),
                rsi=round(float(_rsi(c).iloc[-1]), 0),
                atr_pct=round(float(tr.rolling(14).mean().iloc[-1]) / last * 100, 1),
                rv20=round(float(c.pct_change().rolling(20).std().iloc[-1])
                           * math.sqrt(252) * 100, 0),
                r5=round((last / c.iloc[-6] - 1) * 100, 1),
                r21=round((last / c.iloc[-22] - 1) * 100, 1),
                r63=round((last / c.iloc[-64] - 1) * 100, 1),
                dd52=round((last / c.rolling(252).max().iloc[-1] - 1) * 100, 1),
                sma200=round(float(c.rolling(200).mean().iloc[-1]), 2),
            )
            r["flags"] = _flags(r)
            out["rows"].append(r)
        except Exception:
            continue

    d = out["derived"]
    try:
        ratio = closes["RSP"] / closes["SPY"]
        d["breadth_rsp_spy_21d_pct"] = round((ratio.iloc[-1] / ratio.iloc[-22] - 1) * 100, 1)
        d["iwm_tlt_corr21"] = round(float(closes["IWM"].pct_change().tail(21)
                                          .corr(closes["TLT"].pct_change().tail(21))), 2)
        d["gld_10y_corr42"] = round(float(closes["GLD"].pct_change().tail(42)
                                          .corr(closes["^TNX"].diff().tail(42))), 2)
        d["hyg_21d_pct"] = round((closes["HYG"].iloc[-1] / closes["HYG"].iloc[-22] - 1) * 100, 1)
        d["vix_term_ratio"] = round(float(closes["^VIX"].iloc[-1] / closes["^VIX3M"].iloc[-1]), 2)
        d["us10y_yield"] = round(float(closes["^TNX"].iloc[-1]), 2)
        d["us10y_21d_chg_pp"] = round(float(closes["^TNX"].iloc[-1])
                                      - float(closes["^TNX"].iloc[-22]), 2)
        uso = closes["USO"]
        d["uso_2y_percentile"] = round(float((uso.iloc[-1] - uso.min())
                                             / (uso.max() - uso.min()) * 100), 0)
    except Exception as exc:
        d["error"] = str(exc)
    return out


def render(q: dict) -> str:
    hdr = (f"{'ticker':<8}{'px':>10}{'vs20':>6}{'vs50':>6}{'vs200':>7}{'rsi':>5}"
           f"{'atr%':>6}{'rv20':>6}{'r5':>6}{'r21':>6}{'r63':>7}{'dd52':>7}  flags")
    lines = [f"QUANT PASS  {q['as_of']}", q["source"], "=" * len(hdr), hdr]
    for r in q["rows"]:
        lines.append(f"{r['ticker']:<8}{r['px']:>10}{r['vs20']:>6}{r['vs50']:>6}"
                     f"{r['vs200']:>7}{r['rsi']:>5.0f}{r['atr_pct']:>6}{r['rv20']:>6.0f}"
                     f"{r['r5']:>6}{r['r21']:>6}{r['r63']:>7}{r['dd52']:>7}  {r['flags']}")
    lines.append("\n-- DERIVED CROSS-ASSET --")
    for k, v in q["derived"].items():
        lines.append(f"  {k}: {v}")
    return "\n".join(lines)


def main() -> int:
    q = build()
    print(render(q))
    if "--json" in sys.argv:
        p = Path(sys.argv[sys.argv.index("--json") + 1])
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(q, indent=2))
        print(f"\n[quant] json → {p}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
