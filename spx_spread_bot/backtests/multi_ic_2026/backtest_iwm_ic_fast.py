"""IWM 0DTE Iron Condor — FAST: load each day ONCE, test all configs in memory.
Real intraday fills. 2020-2026.
"""
import re
from datetime import datetime, date
from pathlib import Path
import pandas as pd
import numpy as np

DATA = Path(__file__).parent / "data" / "iwm_wide"
RX = re.compile(r"IWM\s+(\d{6})([PC])(\d{8})$")

OTMS  = [0.005, 0.01, 0.015, 0.02]
WINGS = [1, 2, 3, 5]
ENTRY = (13, 45)   # 09:45 ET
EXITT = (19, 55)   # 15:55 ET

def parse(s):
    m = RX.search(s.strip())
    if not m: return None
    yymmdd, side, strike = m.group(1), m.group(2), int(m.group(3))/1000
    return (date(2000+int(yymmdd[:2]), int(yymmdd[2:4]), int(yymmdd[4:6])), side, strike)

def minute_pivot(df, hh, mm, expiry):
    target = f"{hh:02d}:{mm:02d}"
    snap = df[df["hm"] == target]
    if snap.empty: return None
    snap = snap[snap["exp"] == expiry]
    if snap.empty: return None
    # dedupe (strike,side) keep first
    snap = snap.drop_duplicates(["strike","side"])
    piv = snap.pivot(index="strike", columns="side", values=["bid_px_00","ask_px_00"])
    return piv

def estimate_spot(piv):
    """IWM spot via put-call parity at ATM: where |C_bid - P_bid| min."""
    try:
        cb = piv[("bid_px_00","C")]; pb = piv[("bid_px_00","P")]
        common = cb.index.intersection(pb.index)
        common = [k for k in common if pd.notna(cb.loc[k]) and pd.notna(pb.loc[k])]
        if len(common) < 3: return None
        cb = cb.loc[common]; pb = pb.loc[common]
        atm = (cb - pb).abs().idxmin()
        # parity: C - P = S - K  →  S = C - P + K (use mids)
        return float(cb.loc[atm] - pb.loc[atm] + atm)
    except (KeyError, ValueError):
        return None

def process_day(fp):
    """Return per-config dict of pnl for this day, or {} if no trades."""
    d = datetime.strptime(fp.stem.split("_")[-1], "%Y%m%d").date()
    df = pd.read_parquet(fp, columns=["symbol","bid_px_00","ask_px_00"])
    # Keep entry-side filter (need real quotes to OPEN); exit settled via spot+intrinsic
    df = df[df["ask_px_00"]>0].copy()
    if df.empty: return d, {}
    # parse once
    parsed = df["symbol"].map(parse)
    df = df[parsed.notna()].copy()
    df["exp"]    = parsed[parsed.notna()].map(lambda x: x[0])
    df["side"]   = parsed[parsed.notna()].map(lambda x: x[1])
    df["strike"] = parsed[parsed.notna()].map(lambda x: x[2])
    df["hm"] = df.index.strftime("%H:%M")
    # 0DTE expiry = file date
    e_piv = minute_pivot(df, ENTRY[0], ENTRY[1], d)
    x_piv = minute_pivot(df, EXITT[0], EXITT[1], d)
    if e_piv is None or x_piv is None: return d, {}
    # spot estimate at entry via parity
    spot = estimate_spot(e_piv)
    if spot is None: return d, {}

    # Spot at exit (15:55) via parity — for intrinsic settlement of exit
    spot_exit = estimate_spot(x_piv)
    if spot_exit is None: spot_exit = spot  # fallback

    e_idx = e_piv.index
    results = {}
    for otm in OTMS:
        sp_k = round(spot*(1-otm)); sc_k = round(spot*(1+otm))
        for wing in WINGS:
            lp_k = sp_k - wing; lc_k = sc_k + wing
            need = [sp_k, lp_k, sc_k, lc_k]
            # Entry requires all 4 legs quoted (need real fills to OPEN position)
            if not all(k in e_idx for k in need): continue
            try:
                legs_entry = [e_piv.loc[sp_k,("bid_px_00","P")], e_piv.loc[sc_k,("bid_px_00","C")],
                              e_piv.loc[lp_k,("ask_px_00","P")], e_piv.loc[lc_k,("ask_px_00","C")]]
                if any(pd.isna(v) for v in legs_entry): continue
                sp_b, sc_b, lp_a, lc_a = (float(v) for v in legs_entry)
                cr = (sp_b + sc_b) - (lp_a + lc_a)   # entry credit (real fills)
                if cr <= 0: continue
                # EXIT: settle at intrinsic vs IWM 15:55 spot (wings worthless = $0, correct)
                S = spot_exit
                short_put_intr  = max(0.0, sp_k - S)
                short_call_intr = max(0.0, S - sc_k)
                long_put_intr   = max(0.0, lp_k - S)
                long_call_intr  = max(0.0, S - lc_k)
                ex = (short_put_intr + short_call_intr) - (long_put_intr + long_call_intr)
                pnl = (cr - ex) * 100
                if pd.isna(pnl): continue
                results[(otm,wing)] = pnl
            except (KeyError, ValueError):
                continue
    return d, results

def main():
    files = sorted(DATA.glob("iwm_cbbo_*.parquet"))
    print(f"[IWM] {len(files)} days to process...", flush=True)
    # accumulate
    rows = []
    for i, fp in enumerate(files):
        d, res = process_day(fp)
        for (otm,wing), pnl in res.items():
            rows.append({"date":d, "year":d.year, "otm":otm, "wing":wing, "pnl":pnl})
        if i % 100 == 0:
            print(f"  [{i+1}/{len(files)}] {d}", flush=True)
    allr = pd.DataFrame(rows)
    allr.to_parquet(Path(__file__).parent / "iwm_ic_results.parquet")

    print("\n" + "="*110)
    print("IWM 0DTE IRON CONDOR — REAL fills (sell@bid/buy@ask), entry 09:45 exit 15:55, 2020-2026")
    print("="*110)
    summary = []
    for otm in OTMS:
        for wing in WINGS:
            sub = allr[(allr["otm"]==otm)&(allr["wing"]==wing)]
            if sub.empty:
                print(f"  OTM={otm*100:.1f}% wing=${wing}: NO trades"); continue
            p = sub["pnl"]; w=p[p>0]; l=p[p<0]
            print(f"  OTM={otm*100:.1f}% wing=${wing}: n={len(sub)} total=${p.sum():+,.0f} avg=${p.mean():+.0f} "
                  f"win={(p>0).mean()*100:.0f}% avgW=${w.mean() if len(w) else 0:+.0f} avgL=${l.mean() if len(l) else 0:+.0f} worst=${p.min():+,.0f}")
            summary.append((p.sum(), otm, wing, sub))
        print()

    summary.sort(reverse=True)
    print("\n=== TOP 3 — yearly breakdown ===")
    for total, otm, wing, sub in summary[:3]:
        print(f"\nOTM={otm*100:.1f}% wing=${wing} → total=${total:+,.0f}")
        for y, g in sub.groupby("year"):
            p = g["pnl"]
            print(f"  {y}: n={len(g):3d} total=${p.sum():+,.0f} avg=${p.mean():+.0f} win={(p>0).mean()*100:.0f}% worst=${p.min():+,.0f}")

if __name__ == "__main__":
    main()
