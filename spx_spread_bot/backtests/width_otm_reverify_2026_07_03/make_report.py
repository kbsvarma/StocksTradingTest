"""Build REPORT.md from reverify_metrics.csv + trades_corrected.csv.

Adds split-half stability (2014-2019 vs 2020-2026) and the final VERDICT.
"""
import pandas as pd, numpy as np
from pathlib import Path

OUT = Path("/Users/varmakammili/Documents/GitHub/StocksTradingTest/spx_spread_bot/backtests/width_otm_reverify_2026_07_03")
MULT = 100.0

met = pd.read_csv(OUT / "reverify_metrics.csv")
tr = pd.read_csv(OUT / "trades_corrected.csv")
tr["date"] = pd.to_datetime(tr["date"])

def cfg_label(w, otm, n):
    return f"{int(w)}pt × {int(n)} @ {otm*100:.2f}% OTM"

def daily_sharpe(net):
    mu, sd = net.mean(), net.std(ddof=1)
    return (mu / sd * np.sqrt(252)) if sd and sd > 0 else 0.0

# focus: min_credit=1.50 (live-bot floor), slip=0.10 (primary)
PRIMARY_SLIP = 0.10
PRIMARY_MC = 1.50

lines = []
def P(s=""): lines.append(s)

P("# RE-VERIFICATION REPORT — SPX 0DTE width×OTM (2026-07-03)")
P()
P("Re-audit of `width_otm_research_2026_05_14/` under executable CBBO quotes,")
P("realistic stop fills, correct PM settlement, honest daily Sharpe, and the live")
P("bot's own $1.50 (SPX-equiv) min-credit floor. See `audit_notes.md` for the bugs.")
P()

# ── ORIGINAL vs CORRECTED headline table ──
orig = {
    (5, 0.005, 10):  dict(total=643780, wr=0.8537, sharpe=3.45, worst=-3100),
    (5, 0.0075, 10): dict(total=615890, wr=0.8913, sharpe=6.49, worst=-3100),
    (10, 0.005, 5):  dict(total=468680, wr=0.8641, sharpe=4.66, worst=-3050),
    (50, 0.010, 1):  None,  # not in original top-5; baseline
}

P("## Original claims vs corrected (min_credit=$1.50, slip=$0.10/side)")
P()
P("| config | ORIG total | ORIG WR | ORIG Sharpe | CORR trades | CORR total | CORR avg/trade | CORR WR | honest daily Sharpe | per-trade Sharpe | worst day | max DD |")
P("|---|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|")

prim = met[(met["slip"] == PRIMARY_SLIP) & (met["min_credit"] == PRIMARY_MC)]
def get_row(w, otm, n):
    r = prim[(prim["width"] == w) & (np.isclose(prim["otm_pct"], otm)) & (prim["n_contracts"] == n)]
    return r.iloc[0] if len(r) else None

for (w, otm, n) in [(5,0.005,10),(5,0.0075,10),(10,0.005,5),(50,0.010,1)]:
    o = orig.get((w, otm, n))
    r = get_row(w, otm, n)
    otxt = (f"${o['total']:,} | {o['wr']:.1%} | {o['sharpe']:.2f}" if o else "— | — | —")
    if r is not None:
        P(f"| {cfg_label(w,otm,n)} | {otxt} | {int(r['trades'])} | "
          f"${r['total_pnl']:,.0f} | ${r['avg_pnl']:,.0f} | {r['win_rate']:.1%} | "
          f"{r['sharpe_daily_honest']:.2f} | {r['sharpe_pertrade_origstyle']:.2f} | "
          f"${r['worst_day']:,.0f} | ${r['max_dd']:,.0f} |")
    else:
        P(f"| {cfg_label(w,otm,n)} | {otxt} | 0 | (no qualifying trades) | | | | | | |")
P()

# ── slippage sensitivity ──
P("## Slippage & min-credit sensitivity")
P()
P("| min_credit | slip/side | config | trades | total | avg/trade | WR | daily Sharpe | worst | maxDD |")
P("|--:|--:|---|--:|--:|--:|--:|--:|--:|--:|")
for mc in sorted(met["min_credit"].unique()):
    for slip in sorted(met["slip"].unique()):
        sub = met[(met["min_credit"] == mc) & (met["slip"] == slip)]
        for _, r in sub.iterrows():
            P(f"| ${mc:.2f} | ${slip:.2f} | {cfg_label(r['width'],r['otm_pct'],r['n_contracts'])} | "
              f"{int(r['trades'])} | ${r['total_pnl']:,.0f} | ${r['avg_pnl']:,.0f} | {r['win_rate']:.1%} | "
              f"{r['sharpe_daily_honest']:.2f} | ${r['worst_day']:,.0f} | ${r['max_dd']:,.0f} |")
P()

# ── split-half stability (primary cell) ──
P("## Split-half stability (min_credit=$1.50, slip=$0.10)")
P()
P("| config | 2014-2019 trades | 2014-2019 total | 2014-2019 avg | 2020-2026 trades | 2020-2026 total | 2020-2026 avg |")
P("|---|--:|--:|--:|--:|--:|--:|")
prim_tr = tr[(tr["slip"] == PRIMARY_SLIP) & (tr["min_credit"] == PRIMARY_MC)]
for (w, otm, n) in [(5,0.005,10),(5,0.0075,10),(10,0.005,5),(50,0.010,1)]:
    sub = prim_tr[(prim_tr["width"] == w) & (np.isclose(prim_tr["otm_pct"], otm)) & (prim_tr["n_contracts"] == n)].copy()
    if sub.empty:
        P(f"| {cfg_label(w,otm,n)} | 0 | — | — | 0 | — | — |")
        continue
    sub["net"] = sub["pnl_per_spread"] * n
    a = sub[sub["date"] < "2020-01-01"]
    b = sub[sub["date"] >= "2020-01-01"]
    def blk(x):
        return (len(x), x["net"].sum() if len(x) else 0, x["net"].mean() if len(x) else 0)
    na, ta, aa = blk(a); nb, tb, ab = blk(b)
    P(f"| {cfg_label(w,otm,n)} | {na} | ${ta:,.0f} | ${aa:,.0f} | {nb} | ${tb:,.0f} | ${ab:,.0f} |")
P()

# ── VERDICT (filled by hand-in-code below using primary cell) ──
b5 = get_row(5, 0.005, 10)
b50 = get_row(50, 0.010, 1)

P("## VERDICT")
P()
if b5 is not None and b50 is not None:
    b5t, b50t = b5["total_pnl"], b50["total_pnl"]
    P(f"**Original $643,780 / 85.4% WR / Sharpe 3.45 for 5pt×10 @ 0.50% is FABRICATED** by "
      f"(1) a look-ahead direction filter that gates entry on the 16:00 close, "
      f"(2) entry/exit priced off daily OHLCV trade-prints instead of executable quotes, and "
      f"(3) a hard-coded 2.5×credit stop fill with no real quote path. "
      f"Under executable CBBO fills + PM settle + the live bot's own $1.50 credit floor, "
      f"the narrow 5pt×10 @ 0.50% config books ${b5t:,.0f} vs the 1×50pt baseline's ${b50t:,.0f}.")
P()
P(f"See VERDICT_LINE below for the one-line summary.")
P()

(OUT / "REPORT.md").write_text("\n".join(lines))
print("\n".join(lines))
print("\n\n=== KEY NUMBERS ===")
if b5 is not None:
    print("5pt×10 @0.50%:", dict(trades=int(b5['trades']), total=round(b5['total_pnl']), avg=round(b5['avg_pnl']),
          wr=round(b5['win_rate'],3), sharpe_daily=round(b5['sharpe_daily_honest'],2), worst=round(b5['worst_day'])))
if b50 is not None:
    print("50pt×1 @1.0% (baseline):", dict(trades=int(b50['trades']), total=round(b50['total_pnl']), avg=round(b50['avg_pnl']),
          wr=round(b50['win_rate'],3), sharpe_daily=round(b50['sharpe_daily_honest'],2), worst=round(b50['worst_day'])))
