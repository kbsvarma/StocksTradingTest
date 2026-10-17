"""R2 core + breadth satellite at 1 and 3 lots, satellite confirmation
conditioned on R2's OWN exposure path (not the bare core's)."""
import sys
from pathlib import Path
import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path[:0] = [str(ROOT / "reentry_regime_2026_07" / "codex_validation"),
                str(ROOT / "ridge_volume_audit_2026_07")]
import audit_and_extend as audit

audit.rg.START, audit.rg.SCFG = audit.START, dict(audit.PRODUCTION)
bars = {s: audit.load_60m(s) for s in audit.SYMS}
original = audit.feature_frames(bars)
features, _ = audit.add_volume_features(bars, original)
teachers = audit.extended_teachers(bars, features)
policy, _ = audit.policy_and_base(bars)
pred_r2 = audit.generic_predictions(bars, features, teachers, audit.PLAIN)
R2 = audit.simulate_variant(pred_r2, policy, bars)
r2_pnl = audit.hourly_mtm(R2, bars, 3.5)
print("R2 core rebuilt:", audit.path_stats(r2_pnl, R2), flush=True)

EXPO = (pd.concat([
    pd.Series(R2.qty.to_numpy(), index=pd.DatetimeIndex(R2.entry_time)),
    pd.Series(-R2.qty.to_numpy(), index=pd.DatetimeIndex(R2.exit_time))])
    .groupby(level=0).sum().sort_index().cumsum())

# ---- breadth satellite (exact codex cell), confirmation vs R2 exposure
sys.path.insert(0, str(ROOT / "reentry_regime_2026_07"))
import strategy
from rotation import monday_of_week
rg = audit.rg
daily = {s: bars[s]["close"].resample("1D").last().dropna() for s in bars}
W = rg.weekly_weights(daily, 35, "aggressive300",
                      min(b.index.min() for b in bars.values()),
                      max(b.index.max() for b in bars.values()))
WROWS = {m: W.loc[m] for m in W.index}


def benched(sym, ts):
    row = WROWS.get(monday_of_week(ts))
    return (row is not None and int(row[sym]) == 0
            and sum(int(row[s]) == 0 for s in bars) < 3)


def day_sat(sym):
    B = bars[sym]
    sig = strategy.signal_frame(B, rg.SCFG)
    atr = sig["atr"].values
    vol_ok = sig["vol_ok"].values
    et = B.index.tz_convert("America/New_York")
    hours = et.hour.values
    dates = et.date
    eligible = (hours >= 9) & (hours < 16)
    opens, highs, lows, closes = (B[c].values for c in
                                  ("open", "high", "low", "close"))
    rolls = B["roll"].values
    ts = B.index
    roll_dates = set(dates[rolls == 1])
    last_elig = {}
    for i in range(len(B)):
        if eligible[i]:
            last_elig[dates[i]] = i
    trades = []
    pos = pend = 0
    e = hw = st = np.nan
    e_ts = None
    cur = None
    shigh, nbars, done = -np.inf, 0, False
    for i in range(len(B)):
        if dates[i] != cur:
            cur = dates[i]
            shigh, nbars, done = -np.inf, 0, False
            pend = pos = 0
        if not eligible[i]:
            continue
        if pend:
            pos, pend = 1, 0
            e = hw = opens[i]
            st = e - 2.0 * atr[i - 1]
            e_ts = ts[i]
            if np.isnan(st):
                pos = 0
        if pos:
            if lows[i] <= st:
                x = min(st, opens[i])
                trades.append((e_ts, ts[i], sym, e, x, x - e, "stop"))
                pos = 0
            elif i == last_elig[cur]:
                trades.append((e_ts, ts[i], sym, e, closes[i],
                               closes[i] - e, "eod"))
                pos = 0
            else:
                hw = max(hw, highs[i])
                st = max(st, hw - 2.0 * atr[i])
        if (pos == 0 and pend == 0 and not done and nbars >= 2
                and cur not in roll_dates and benched(sym, ts[i])
                and np.isfinite(atr[i]) and vol_ok[i]
                and closes[i] > shigh + 0.25 * atr[i]
                and i + 1 < len(B) and eligible[i + 1]
                and dates[i + 1] == cur and hours[i + 1] < 14):
            loc = EXPO.index.searchsorted(ts[i + 1], side="right") - 1
            if loc >= 0 and EXPO.iloc[loc] > 0:
                pend = 1
                done = True
        shigh = max(shigh, highs[i])
        nbars += 1
    return pd.DataFrame(trades, columns=["entry_time", "exit_time", "sym",
                                         "entry_px", "exit_px", "points",
                                         "reason"])


S = pd.concat([day_sat(s) for s in bars])
S = S[S["entry_time"] >= "2011-01-01"]
r2_25 = r2_pnl[r2_pnl.index < "2026-01-01"]
r2_daily = r2_25.groupby(r2_25.index.normalize()).sum()

for lots in (1, 3):
    P = S.copy()
    P["pnl"] = (P["points"] * P["sym"].map(rg.MULT) - rg.FR) * lots
    P25 = P[P["exit_time"] < "2026-01-01"]
    sp = pd.Series(P25["pnl"].values,
                   index=pd.DatetimeIndex(P25["exit_time"])).sort_index()
    sd = sp.groupby(sp.index.normalize()).sum()
    comb = r2_daily.add(sd, fill_value=0.0)
    eq = comb.cumsum()
    eq_dd = float((eq - eq.cummax().clip(lower=0)).min())
    yearly = comb.groupby(comb.index.year).sum()
    print(f"\nR2 + satellite x{lots}: sat n={len(P25)} sat ${sp.sum():,.0f}")
    print(f"  combined 2011-25 total ${comb.sum():,.0f}  "
          f"maxDD ${eq_dd:,.0f}  ratio {comb.sum()/abs(eq_dd):.2f}  "
          f"worst-yr ${yearly.min():,.0f} ({yearly.idxmin()})")
    print(f"  halves {comb[:'2017'].sum():,.0f} / {comb['2018':].sum():,.0f}"
          f"   max book exposure {3 + 2*lots} lots")
r2y = r2_daily.groupby(r2_daily.index.year).sum()
print(f"\nR2 alone: total ${r2_daily.sum():,.0f}  worst ${r2y.min():,.0f}")
