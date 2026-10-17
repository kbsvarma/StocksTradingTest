"""STEP 1 DIAGNOSTIC (declared 2026-07-20, user-initiated): does the gold
sleeve's 'green first, dies at the stop' pattern generalize across history?

DIAGNOSTIC ONLY — no parameter search, no config claim. Measures, for every
historical GC/MGC trade under the LIVE bot config (Donchian-20 close breakout,
ATR-20, 2.0x chandelier, vol q95 + 02-06 ET session gates, roll force-close,
$10/pt MGC economics, $3.50 RT):
  - MFE (max favorable excursion) in R units (R = initial stop distance
    = 2*ATR at entry), counted on bars STRICTLY BEFORE the exit bar for
    stop exits (conservative: a 60m bar cannot order intrabar prints)
  - the loser-MFE distribution (the user's observation, quantified)
  - profit concentration of the right tail (what a profit target would cap)

Segments run separately (no splice seam):
  A: GC 1-min 2010-06..2019-06 (convex cache), trades 2011+  [the W2 decade]
  B: MGC 1-min 2019..2026-07 (gold_london csv + topup), trades 2019-10+
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "trend_bot"))

import strategy  # noqa: E402

SCFG = dict(donchian_n=20, atr_n=20, k_trail=2.0, vol_gate_q=0.95,
            vol_gate_window_bars=24 * 365, vol_gate_min_bars=24 * 60,
            session_block_et=(2, 6))
MULT, FR = 10.0, 3.50
LEVELS = [0.25, 0.5, 0.75, 1.0, 1.5, 2.0, 3.0]


def to_60m(df):
    g = df.resample("60min", label="left")
    B = pd.DataFrame({"open": g["open"].first(), "high": g["high"].max(),
                      "low": g["low"].min(), "close": g["close"].last(),
                      "roll": g["roll_flag"].max()}).dropna(subset=["close"])
    B["roll"] = B["roll"].fillna(0).astype(int)
    return B


def seg_a():
    df = pd.read_parquet(
        ROOT / "convex_research_2026_07/data/gc_1m_2010_2019.parquet")
    df = df.rename(columns=str.lower)
    df["roll_flag"] = (df["instrument_id"] != df["instrument_id"].shift()) \
        .astype(int)
    df.iloc[0, df.columns.get_loc("roll_flag")] = 0
    return to_60m(df), pd.Timestamp("2011-01-01", tz="UTC")


def seg_b():
    a = pd.read_csv(ROOT / "spx_spread_bot/backtests/gold_london/data/mgc_1m.csv",
                    index_col=0)
    a.index = pd.to_datetime(a.index, utc=True)
    b = pd.read_parquet(
        ROOT / "gold_research_2026_07/data/mgc_1m_topup_2026.parquet")
    b = b.rename(columns=str.lower)[["open", "high", "low", "close"]]
    df = pd.concat([a[["open", "high", "low", "close"]], b]).sort_index()
    df = df[~df.index.duplicated()]
    d1 = pd.read_parquet(
        ROOT / "gold_research_2026_07/data/mgc_1d_2019_2026.parquet")
    rolls = d1.index[d1["instrument_id"] != d1["instrument_id"].shift()][1:]
    df["roll_flag"] = 0
    for rd in rolls:
        df.loc[(df.index >= rd) & (df.index < rd + pd.Timedelta(days=1)),
               "roll_flag"] = 1
    return to_60m(df), pd.Timestamp("2019-10-01", tz="UTC")


def simulate_mfe(B, start, mult=MULT):
    f = strategy.signal_frame(B, SCFG)
    sig = f["signal"].values
    atr = f["atr"].values
    O, H, L, C = (B[c].values for c in ("open", "high", "low", "close"))
    R_, ts = B["roll"].values, B.index
    rows = []
    pos = pend = 0
    e = hw = st = sa = mfe = np.nan
    e_ts = None
    for i in range(len(B)):
        if pend and R_[i]:
            pend = 0
        if pend:
            pos, pend = 1, 0
            e, hw, sa = O[i], O[i], atr[i - 1]
            st = e - SCFG["k_trail"] * sa
            mfe = 0.0     # highs count only on bars where no exit fires
            e_ts = ts[i]
            if np.isnan(st):
                pos = 0
        if pos and R_[i]:
            x = C[i - 1] if i else O[i]
            rows.append((e_ts, ts[i], e, x, x - e, sa, mfe, "roll"))
            pos = 0
        if pos:
            if L[i] <= st:
                x = min(st, O[i])         # exit bar's high NOT counted (conservative)
                rows.append((e_ts, ts[i], e, x, x - e, sa, mfe, "stop"))
                pos = 0
            else:
                mfe = max(mfe, H[i] - e)
                hw = max(hw, H[i])
                st = max(st, hw - SCFG["k_trail"] * atr[i])
        if pos == 0 and pend == 0 and sig[i] and i > SCFG["donchian_n"]:
            pend = 1
    T = pd.DataFrame(rows, columns=["entry_time", "exit_time", "entry", "exit",
                                    "points", "sa", "mfe_px", "reason"])
    T = T[T["entry_time"] >= start].reset_index(drop=True)
    T["pnl"] = T["points"] * mult - FR
    T["R"] = 2.0 * T["sa"]                 # initial dollar risk in points
    T["mfe_R"] = T["mfe_px"] / T["R"]
    T["final_R"] = T["points"] / T["R"]
    return T


def describe(tag, T, mult=MULT):
    lose = T[T["pnl"] < 0]
    win = T[T["pnl"] >= 0]
    print(f"\n=== {tag}: {len(T)} trades  total ${T['pnl'].sum():,.0f}  "
          f"win% {len(win) / len(T) * 100:.1f}  "
          f"({T['entry_time'].min().date()} -> {T['exit_time'].max().date()})")
    print(f"  winners ${win['pnl'].sum():,.0f} | losers ${lose['pnl'].sum():,.0f}")
    print(f"  LOSERS ({len(lose)}): median MFE {lose['mfe_R'].median():.2f}R")
    for lv in LEVELS:
        pl = (lose["mfe_R"] >= lv).mean() * 100
        pa = (T["mfe_R"] >= lv).mean() * 100
        # oracle: bank exactly +lv R on every trade that reaches it, else
        # take the actual outcome — the IMPOSSIBLE upper frame, no search
        oracle = np.where(T["mfe_R"] >= lv, lv * T["R"] * mult - FR, T["pnl"])
        print(f"  reached >= {lv:>4}R: losers {pl:5.1f}%  all {pa:5.1f}%  "
              f"| bank-at-{lv}R counterfactual total ${oracle.sum():>9,.0f}")
    gross = T.loc[T["pnl"] > 0, "pnl"].sum()
    for fr in (2.0, 4.0):
        share = T.loc[T["final_R"] >= fr, "pnl"].sum() / gross * 100
        print(f"  share of gross profit from trades finishing >= {fr}R: "
              f"{share:.1f}%")
    top5 = T.nlargest(5, "pnl")["pnl"].sum() / gross * 100
    print(f"  top-5 trades = {top5:.1f}% of gross profit")
    return T


def main():
    all_T = []
    for tag, (B, start) in (("A 2011-2019 (GC decade)", seg_a()),
                            ("B 2019-2026 (MGC modern)", seg_b())):
        T = simulate_mfe(B, start)
        describe(tag, T)
        all_T.append(T)
    T = pd.concat(all_T, ignore_index=True)
    describe("COMBINED 2011-2026", T)
    out = Path(__file__).parent / "mfe_trades.parquet"
    T.to_parquet(out)
    print(f"\nwrote {out}")


if __name__ == "__main__":
    main()
