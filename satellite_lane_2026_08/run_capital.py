"""TRUE capital requirement for the 1-contract variants.

Replaces the crude "peak margin + maxDD" sum with the actual binding
constraint: starting capital E must satisfy, for EVERY day t,
    E + cumulative_PnL(t) >= margin_required(t)
i.e.  E >= max_t [ margin(t) - cumPnL(t) ].
Early drawdown binds hardest (no profit buffer yet). Reports the binding
date and the margin/PnL at that moment so the number is auditable.
"""
from pathlib import Path
import pandas as pd

HERE = Path(__file__).resolve().parent
src = open(HERE / "run_subhourly_exhaust.py").read()
exec(src[:src.index('run("ANCHOR")')])

from rotation import monday_of_week, weights_for                # noqa: E402
from rotation_grid import trailing_returns                      # noqa: E402
exec(open(HERE / "run_onelot.py").read().split("results = []")[0]
     .split('exec(src[:src.index(\'run("ANCHOR")\')])')[1])      # helpers

# IBKR-style overnight maintenance per contract (conservative, Aug 2026)
OVN = {"NQ": 2300.0, "ES": 1600.0, "YM": 1100.0}
INTRA = {k: v * 0.60 for k, v in OVN.items()}     # satellite is intraday


def capital_path(core_t, sat_t, label):
    days = pd.date_range("2011-01-01", "2025-12-31", freq="D", tz="UTC")
    margin = pd.Series(0.0, index=days)
    # A symbol holds at most ONE position at a time: take the MAX qty open
    # on each day per symbol, never the sum across its trades (an exit and a
    # re-entry on the same day are sequential, not concurrent).
    for sym in OVN:
        legs = core_t[core_t["sym"] == sym]
        if len(legs):
            q = pd.Series(0.0, index=days)
            for tr in legs.itertuples():
                live = ((days >= pd.Timestamp(tr.entry_time).normalize()) &
                        (days <= pd.Timestamp(tr.exit_time).normalize()))
                q[live] = q[live].clip(lower=float(tr.qty))
            margin += q * OVN[sym]
        s_legs = sat_t[sat_t["sym"] == sym]       # intraday, 1 lot, 1 at a time
        if len(s_legs):
            sd = pd.DatetimeIndex(s_legs["entry_time"]).normalize().unique()
            margin[margin.index.isin(sd)] += INTRA[sym]
    # cumulative realised P&L by day (booked at exit)
    pnl = pd.concat([
        pd.Series(core_t["pnl"].values,
                  index=pd.DatetimeIndex(core_t["exit_time"]).normalize()),
        pd.Series(sat_t["pnl"].values,
                  index=pd.DatetimeIndex(sat_t["exit_time"]).normalize())])
    cum = pnl.groupby(level=0).sum().reindex(days, fill_value=0.0).cumsum()
    need = margin - cum
    i = need.idxmax()
    print(f"{label:4} required E = ${need.max():,.0f}   "
          f"binding {i.date()}  (margin ${margin[i]:,.0f} "
          f"- cumPnL ${cum[i]:,.0f})   peak-margin-ever ${margin.max():,.0f}")
    return float(need.max())


out = {}
for kind in ("V1", "V2", "V3", "BASE"):
    Wv = build_weights(kind)
    globals()["W"] = Wv
    globals()["WROWS"] = {m: Wv.loc[m] for m in Wv.index}
    C = core_for(Wv)
    globals()["EXPO"] = expo_for(C)
    ct = C[(C["exit_time"] >= "2011-01-01") & (C["exit_time"] < "2026-01-01")]
    S = pd.concat([day_sat(s, cutoff=15) for s in M1])
    st = S[(S["exit_time"] >= "2011-01-01") & (S["exit_time"] < "2026-01-01")]
    out[kind] = capital_path(ct, st, kind)
print()
print("=== with a 25% safety buffer (future DD may exceed the past) ===")
for k, v in out.items():
    print(f"{k:4} ${v:,.0f} raw  ->  ${v*1.25:,.0f} recommended")
print("DONE")
