"""Yearly P&L: live-config and R2 cores, each +/- the RECOMMENDED satellite
(15m signals, early window, entries<15:00, 2xATR60 stop, flat 16:00).
Satellite confirmation conditioned on EACH core's own exposure path."""
import sys
from pathlib import Path
import pandas as pd

src = open("run_subhourly_exhaust.py").read()
exec(src[:src.index('run("ANCHOR")')])          # loads, CORE, EXPO, day_sat

REC = dict(freq="15min", cutoff=15)             # rest = anchor defaults

# satellite vs BARE core exposure (EXPO already = bare core)
S_live = pd.concat([day_sat(s, **REC) for s in M1])

# R2 core via the audited ridge pipeline
sys.path[:0] = [str(HERE.parent / "reentry_regime_2026_07" / "codex_validation"),
                str(HERE.parent / "ridge_volume_audit_2026_07")]
import audit_and_extend as audit
audit.rg.START, audit.rg.SCFG = audit.START, dict(audit.PRODUCTION)
abars = {s: audit.load_60m(s) for s in audit.SYMS}
feats, _ = audit.add_volume_features(abars, audit.feature_frames(abars))
teachers = audit.extended_teachers(abars, feats)
policy, _ = audit.policy_and_base(abars)
pred = audit.generic_predictions(abars, feats, teachers, audit.PLAIN)
R2 = audit.simulate_variant(pred, policy, abars)
r2_pnl = audit.hourly_mtm(R2, abars, 3.5)
EXPO = (pd.concat([
    pd.Series(R2.qty.to_numpy(), index=pd.DatetimeIndex(R2.entry_time)),
    pd.Series(-R2.qty.to_numpy(), index=pd.DatetimeIndex(R2.exit_time))])
    .groupby(level=0).sum().sort_index().cumsum())
S_r2 = pd.concat([day_sat(s, **REC) for s in M1])   # day_sat reads global EXPO

core_pnl = pd.Series(CORE["pnl"].values,
                     index=pd.DatetimeIndex(CORE["exit_time"])).sort_index()


def yearly(pnl):
    p = pnl[pnl.index >= "2011-01-01"]
    return p.groupby(p.index.year).sum()


def sat_yearly(S):
    p = pd.Series(S["pnl"].values,
                  index=pd.DatetimeIndex(S["exit_time"])).sort_index()
    return yearly(p)


yl_core = yearly(core_pnl)
yl_r2 = yearly(r2_pnl)
yl_sl = sat_yearly(S_live)
yl_sr = sat_yearly(S_r2)
years = sorted(set(yl_core.index) | set(yl_r2.index))
print(f"{'yr':>5} {'live':>8} {'live+sat':>9} {'R2':>8} {'R2+sat':>9}")
for y in years:
    lc = yl_core.get(y, 0)
    r2v = yl_r2.get(y, 0)
    print(f"{y:>5} {lc:>8,.0f} {lc + yl_sl.get(y, 0):>9,.0f} "
          f"{r2v:>8,.0f} {r2v + yl_sr.get(y, 0):>9,.0f}")
t = lambda s: float(s[s.index <= 2025].sum())
print(f"{'11-25':>5} {t(yl_core):>8,.0f} {t(yl_core)+t(yl_sl):>9,.0f} "
      f"{t(yl_r2):>8,.0f} {t(yl_r2)+t(yl_sr):>9,.0f}")
print(f"sat trades: live-core {len(S_live)}, R2-core {len(S_r2)}")
