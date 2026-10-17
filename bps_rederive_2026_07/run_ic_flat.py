"""Flat-day conditioned Iron Condor on REAL quotes — PREREG_IC_FLAT.md.

Primary: 3 conditioning cells at fixed geometry (11:00 / 1.0% OTM / W50 /
SL 2.0). Robustness: 9 declared neighbours on C_FLATVIX only.
IS 2013-2020 / OOS 2021-2026.
"""
import glob
import sys
import traceback
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from ic_engine import ICConfig, ICDayContext, sim_day_ic       # noqa: E402

ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "reentry_regime_2026_07"))


# ---------- flat-day flags from ES 1-min (identical to PREREG_FLATDAY) -----
def flat_flags():
    import engine_v as ev
    rg = ev.rg
    print("loading ES 1-min for flat flags...", flush=True)
    files = sorted(glob.glob(str(rg.SRC["ES"])))
    df = pd.concat([pd.read_parquet(f) for f in files]).sort_index()
    df = df[~df.index.duplicated(keep="last")][["open", "high", "low", "close"]]
    et = df.index.tz_convert("America/New_York")
    df = df.assign(_d=et.date, _h=et.hour, _m=et.minute)
    rth = df[((df["_h"] > 9) | ((df["_h"] == 9) & (df["_m"] >= 30))) & (df["_h"] < 16)]
    out = {}
    for hh in (11, 12, 13):
        rec = {}
        for d, g in rth.groupby("_d"):
            if len(g) < 200:
                continue
            pre = g[(g["_h"] < hh)]
            if len(pre) < 30:
                continue
            op = float(pre["open"].iloc[0])
            spot = float(pre["close"].iloc[-1])
            rng = (float(pre["high"].max()) - float(pre["low"].min())) / op
            rec[d] = bool(abs(spot / op - 1.0) < 0.0025 and rng < 0.0050)
        out[hh] = rec
        print(f"  T{hh}:00 flat days = {sum(rec.values())}/{len(rec)}", flush=True)
    return out


FLAT = flat_flags()

vix = pd.read_parquet(HERE / "data/vix_daily_cboe.parquet")
vix_chg = vix["close"].pct_change()
spx = pd.read_csv(HERE / "data/spx_daily_cboe.csv",
                  parse_dates=["DATE"]).set_index("DATE")["SPX"]
spx_map = {d.strftime("%Y%m%d"): float(v) for d, v in spx.items()}


def day_tags():
    """Enumerate from chain.py's own constants so paths can't drift.

    Restricted to days whose CALL WING exists — without upper strikes an
    iron condor cannot be built, so those days are not 'no trade', they are
    'not measurable' and must not dilute any rate.
    """
    import chain
    tags = set()
    for d, pat in ((chain.BASE / "spxw_p_cbbo_1m", "spxw_p_cbbo_*.parquet"),
                   (chain.NEWDAYS, "spxw_p_cbbo_*.parquet")):
        if d.exists():
            tags |= {f.stem.replace("spxw_p_cbbo_", "") for f in d.glob(pat)}
    wing = {f.stem.replace("spxw_c_cbbo_", "")
            for f in chain.CALLWING.glob("spxw_c_cbbo_*.parquet")}
    tags &= wing
    return sorted(t for t in tags if t.isdigit() and len(t) == 8)


PRIMARY = ICConfig(entry_hh=11, entry_mm=0, width=50, otm_pct=0.010, sl_mult=2.0)
NEIGHBOURS = [
    ICConfig(11, 0, 50, 0.005, 2.0), ICConfig(11, 0, 50, 0.015, 2.0),
    ICConfig(11, 0, 25, 0.010, 2.0),
    ICConfig(11, 0, 50, 0.010, 0.0), ICConfig(11, 0, 50, 0.010, 3.0),
    ICConfig(12, 0, 50, 0.010, 2.0), ICConfig(13, 0, 50, 0.010, 2.0),
    ICConfig(12, 0, 50, 0.010, 3.0), ICConfig(13, 0, 50, 0.010, 3.0),
]
CFGS = [PRIMARY] + NEIGHBOURS

tags = day_tags()
print(f"option days available: {len(tags)}", flush=True)
rows = []
for i, tag in enumerate(tags):
    if tag not in spx_map:
        continue
    dkey = pd.Timestamp(tag)
    vrow = vix.loc[dkey] if dkey in vix.index else None
    try:
        ctx = ICDayContext(tag, vrow, spx_map[tag])
    except Exception:
        continue
    if not ctx.ok:
        continue
    dt = dkey.date()
    vok = False
    if vrow is not None:
        vc = vix_chg.get(dkey, np.nan)
        vok = bool(12 <= float(vrow["close"]) <= 25 and abs(vc) < 0.05) \
            if vc == vc else False
    for cfg in CFGS:
        try:
            r = sim_day_ic(ctx, cfg)
        except Exception:
            continue
        if r.get("status") not in ("stop", "expire"):
            continue
        r["cfg"] = cfg.label()
        r["is_primary"] = (cfg is PRIMARY)
        r["flat"] = bool(FLAT.get(cfg.entry_hh, {}).get(dt, False))
        r["vixok"] = vok
        r["year"] = int(tag[:4])
        rows.append(r)
    if (i + 1) % 250 == 0:
        print(f"  {i+1}/{len(tags)} days, {len(rows)} sims", flush=True)

D = pd.DataFrame(rows)
D.to_csv(HERE / "ic_flat_trades.csv", index=False)
print(f"\ntotal sims: {len(D)}", flush=True)


def summarise(sub, label):
    if len(sub) < 20:
        return None
    n = len(sub)
    tot = float(sub["pnl_usd"].sum())
    wins = float((sub["pnl_usd"] > 0).mean())
    stops = float((sub["status"] == "stop").mean())
    t = (sub["pnl_usd"].mean() / sub["pnl_usd"].std() * np.sqrt(n)
         if sub["pnl_usd"].std() > 0 else np.nan)
    yr = sub.groupby("year")["pnl_usd"].sum()
    return dict(cell=label, n=n, total=round(tot), mean=round(tot / n, 2),
                win=round(wins * 100, 1), stop=round(stops * 100, 1),
                t=round(float(t), 2), credit=round(float(sub["credit"].mean()), 2),
                worst_yr=round(float(yr.min())) if len(yr) else None)


P = D[D["is_primary"]]
IS = P[P["year"] <= 2020]
OOS = P[P["year"] >= 2021]
print("\n=== PRIMARY (11:00 / 1.0% / W50 / SL2.0) ===")
out = []
for split, name in ((IS, "IS 2013-20"), (OOS, "OOS 2021-26")):
    for sel, lab in ((split, "C_ALL"),
                     (split[split["flat"]], "C_FLAT"),
                     (split[split["flat"] & split["vixok"]], "C_FLATVIX")):
        s = summarise(sel, f"{name} {lab}")
        if s:
            out.append(s)
print(pd.DataFrame(out).to_string(index=False))

print("\n=== CREDIT DIAGNOSTIC (is the premise priced in?) ===")
for name, split in (("IS", IS), ("OOS", OOS)):
    a = split["credit"].mean()
    f = split[split["flat"]]["credit"].mean()
    print(f"{name}: mean credit all ${a:.2f} | flat ${f:.2f} | "
          f"flat is {(f/a-1)*100:+.1f}%")

print("\n=== ROBUSTNESS (C_FLATVIX only, declared neighbours) ===")
rb = []
for cfg in CFGS:
    sub = D[(D["cfg"] == cfg.label()) & D["flat"] & D["vixok"]]
    s = summarise(sub[sub["year"] >= 2021], cfg.label())
    if s:
        rb.append(s)
print(pd.DataFrame(rb).to_string(index=False))
print("DONE")
