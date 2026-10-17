"""Gold sleeve measured, then blended into V2 — per user ask 2026-08-05.

The sleeve is INDEPENDENT of the rotation (fixed 1 lot, outside the weekly
weights), so its P&L/margin can be measured once and added to any cell.

DATA CAVEAT (stated up front): the series is spliced from three sources —
GC 1-min 2010-06..2019-06 (has a symbol column, rolls detectable), MGC
1-min 2019-07..2026-05 (gold_london CSV, NO symbol column, roll handling
UNVERIFIED), MGC 1-min 2026-05..07 top-up. GC and MGC quote the same
$/oz price so the splice is price-continuous; the risk is undetected roll
gaps in the middle segment creating phantom breakouts. Results reported
for the full span AND for the roll-verified 2011-2019 sub-period.
"""
from pathlib import Path
import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
src = open(HERE / "run_subhourly_exhaust.py").read()
exec(src[:src.index('run("ANCHOR")')])

from rotation import monday_of_week                              # noqa: E402
from rotation_grid import trailing_returns                       # noqa: E402

ROOT = HERE.parent
rg.MULT["GC"] = 10.0                       # MGC multiplier


def load_gold():
    a = pd.read_parquet(ROOT / "convex_research_2026_07/data/gc_1m_2010_2019.parquet")
    a = a[["open", "high", "low", "close", "symbol"]].copy()
    a["roll_flag"] = (a["symbol"] != a["symbol"].shift()).astype(int)
    a = a.drop(columns=["symbol"])
    # Roll calendar recovered for free from the DAILY GC.v.0 file: its
    # instrument_id changes at every roll even though the symbol alias is
    # constant. 80 rolls 2010-2026, 35 of them in the 2019+ segment.
    dly = pd.read_parquet(ROOT / "gold_research_2026_07/data/gc_1d_2010_2026.parquet")
    ch = dly["instrument_id"].ne(dly["instrument_id"].shift())
    ROLL_DATES = set(dly.index[ch][1:].date)
    print(f"roll calendar: {len(ROLL_DATES)} roll dates from daily instrument_id")

    b = pd.read_csv(ROOT / "spx_spread_bot/backtests/gold_london/data/mgc_1m.csv")
    # mixed -04:00/-05:00 offsets -> force UTC at parse
    b["timestamp"] = pd.to_datetime(b["timestamp"], utc=True, format="ISO8601")
    b = b.set_index("timestamp")
    b = b[["open", "high", "low", "close"]].copy()
    b["roll_flag"] = pd.Series(b.index.date, index=b.index).isin(
        ROLL_DATES).astype(int)
    c = pd.read_parquet(ROOT / "gold_research_2026_07/data/mgc_1m_topup_2026.parquet")
    c = c[["open", "high", "low", "close"]].copy()
    c["roll_flag"] = pd.Series(c.index.date, index=c.index).isin(
        ROLL_DATES).astype(int)
    g = pd.concat([a[a.index < "2019-07-01"], b[b.index < "2026-05-05"], c])
    g = g[~g.index.duplicated(keep="first")].sort_index()
    return g


G1 = load_gold()
print(f"gold 1-min: {len(G1):,} rows  {G1.index.min()} -> {G1.index.max()}",
      flush=True)
g = G1.resample("60min", label="left")
G = pd.DataFrame({"open": g["open"].first(), "high": g["high"].max(),
                  "low": g["low"].min(), "close": g["close"].last(),
                  "roll": g["roll_flag"].max()}).dropna(subset=["close"])
G["roll"] = G["roll"].fillna(0).astype(int)
print(f"gold 60-min bars: {len(G):,}", flush=True)

GT = rg.simulate_weighted(G, "GC", lambda ts: 1)
GT = GT[GT["qty"] > 0].copy()
GT["pnl"] = (GT["points"] * 10.0 - rg.FR) * GT["qty"]
gt = GT[(GT["exit_time"] >= "2011-01-01") & (GT["exit_time"] < "2026-01-01")]
gp = pd.Series(gt["pnl"].values,
               index=pd.DatetimeIndex(gt["exit_time"])).sort_index()
yr = gp.groupby(gp.index.year).sum()
print()
print("=== GOLD SLEEVE STANDALONE (1 lot MGC, deployed 60m config) ===")
print(f"trades {len(gt)}  total ${gp.sum():,.0f}  pace ${gp.sum()/15:,.0f}/yr")
print(f"win rate {float((gt['pnl'] > 0).mean()):.1%}  "
      f"avg win ${gt[gt['pnl'] > 0]['pnl'].mean():,.0f}  "
      f"avg loss ${gt[gt['pnl'] <= 0]['pnl'].mean():,.0f}")
print(f"best trade ${gt['pnl'].max():,.0f}  worst ${gt['pnl'].min():,.0f}")
print("by year:", {int(k): round(v) for k, v in yr.items()})
h1 = gp[:"2019"].sum()
print(f"roll-VERIFIED 2011-2019: ${h1:,.0f}   unverified 2020-25: "
      f"${gp['2020':].sum():,.0f}")

# ---- blend into V2 -------------------------------------------------------
SPEC_V2 = (1, "all3")
OVN = {"NQ": 2300.0, "ES": 1600.0, "YM": 1100.0}
INTRA = {k: v * 0.60 for k, v in OVN.items()}
GC_OVN = 2100.0                             # MGC overnight maintenance
MONDAYS = list(W.index)


def build_v2():
    rows = {}
    for mon in MONDAYS:
        rets = trailing_returns(daily, mon, 35)
        valid = all(v == v for v in rets.values())
        vals = sorted(rets.values(), reverse=True)
        unsure = (not valid) or (vals[0] - vals[1]) * 100 < 1.0
        rank = sorted(rets, key=lambda s: rets[s], reverse=True)
        rows[mon] = ({s: 1 for s in rets} if unsure
                     else {rank[0]: 1, rank[1]: 0, rank[2]: 0})
    return pd.DataFrame(rows).T.sort_index()


Wv = build_v2()
globals()["W"] = Wv
globals()["WROWS"] = {m: Wv.loc[m] for m in Wv.index}
core = []
for sym in M1:
    wq = lambda ts, s=sym: (int(Wv.loc[monday_of_week(ts), s])
                            if monday_of_week(ts) in Wv.index else 1)
    T = rg.simulate_weighted(native(sym, "60min"), sym, wq)
    T = T[T["qty"] > 0].copy()
    T["pnl"] = (T["points"] * rg.MULT[sym] - rg.FR) * T["qty"]
    core.append(T)
C = pd.concat(core)
globals()["EXPO"] = (pd.concat([
    pd.Series(C["qty"].to_numpy(), index=pd.DatetimeIndex(C["entry_time"])),
    pd.Series(-C["qty"].to_numpy(), index=pd.DatetimeIndex(C["exit_time"]))
]).groupby(level=0).sum().sort_index().cumsum())
ct = C[(C["exit_time"] >= "2011-01-01") & (C["exit_time"] < "2026-01-01")]
S = pd.concat([day_sat(s, cutoff=15) for s in M1])
st = S[(S["exit_time"] >= "2011-01-01") & (S["exit_time"] < "2026-01-01")]
DAYS = pd.date_range("2011-01-01", "2025-12-31", freq="D", tz="UTC")


def measure(with_gold):
    margin = pd.Series(0.0, index=DAYS)
    for sym in OVN:
        legs = ct[ct["sym"] == sym]
        q = pd.Series(0.0, index=DAYS)
        for tr in legs.itertuples():
            live = ((DAYS >= pd.Timestamp(tr.entry_time).normalize()) &
                    (DAYS <= pd.Timestamp(tr.exit_time).normalize()))
            q[live] = q[live].clip(lower=float(tr.qty))
        margin += q * OVN[sym]
        sl = st[st["sym"] == sym]
        sd = pd.DatetimeIndex(sl["entry_time"]).normalize().unique()
        margin[margin.index.isin(sd)] += INTRA[sym]
    trades = [ct, st.assign(qty=1)]
    if with_gold:
        q = pd.Series(0.0, index=DAYS)
        for tr in gt.itertuples():
            live = ((DAYS >= pd.Timestamp(tr.entry_time).normalize()) &
                    (DAYS <= pd.Timestamp(tr.exit_time).normalize()))
            q[live] = q[live].clip(lower=1.0)
        margin += q * GC_OVN
        trades.append(gt.assign(qty=1))
    allt = pd.concat(trades)
    cp = pd.Series(allt["pnl"].values,
                   index=pd.DatetimeIndex(allt["exit_time"])).sort_index()
    cum = (cp.groupby(cp.index.normalize()).sum()
           .reindex(DAYS, fill_value=0.0).cumsum())
    need = float((margin - cum).max())
    dd = daily_mtm_dd(allt)
    y = cp.groupby(cp.index.year).sum()
    return dict(total=float(cp.sum()), dd=dd, ratio=float(cp.sum()) / abs(dd),
                worst=float(y.min()), need=need,
                eff=float(cp.sum()) / 15 / (need / 1000))


print()
print("=== V2 WITHOUT vs WITH the gold sleeve ===")
for lbl, wg in (("V2 (no gold)", False), ("V2 + gold", True)):
    r = measure(wg)
    print(f"{lbl:14} total ${r['total']:>8,.0f}  DD ${r['dd']:>7,.0f}  "
          f"ratio {r['ratio']:>5.2f}  worst ${r['worst']:>7,.0f}  "
          f"need ${r['need']:>7,.0f} (+25% ${r['need']*1.25:>7,.0f})  "
          f"pace ${r['total']/15:>5,.0f}/yr  eff ${r['eff']:.1f}/$1k")
print("DONE")
