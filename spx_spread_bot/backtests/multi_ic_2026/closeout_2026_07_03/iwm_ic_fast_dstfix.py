"""IWM 0DTE IC sweep — EXACT copy of backtest_iwm_ic_fast.py logic with ONE fix:
entry/exit minutes are computed timezone-correctly per date (09:45 / 15:55 ET
-> UTC via zoneinfo) instead of hardcoded 13:45/19:55 UTC. The original
hardcode silently dropped every EST-month day (Nov-Mar), ~40% of the sample.

Everything else identical: natural entry fills (sell@bid/buy@ask), no SL/PT,
hold to 15:55, exit settled at intrinsic vs parity spot.

Crash-resilient: appends per-day rows to results.partial.csv every day,
skips already-done dates on restart, finalizes atomically.
"""
import os
import re
from datetime import datetime, date
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

HERE = Path(__file__).parent
DATA = HERE.parent / "data" / "iwm_wide"
RX = re.compile(r"IWM\s+(\d{6})([PC])(\d{8})$")
ET = ZoneInfo("America/New_York")

OTMS = [0.005, 0.01, 0.015, 0.02]
WINGS = [1, 2, 3, 5]

PARTIAL = HERE / "iwm_dstfix.partial.csv"
FINAL = HERE / "iwm_dstfix_results.csv"


def parse(s):
    m = RX.search(s.strip())
    if not m:
        return None
    yymmdd, side, strike = m.group(1), m.group(2), int(m.group(3)) / 1000
    return (date(2000 + int(yymmdd[:2]), int(yymmdd[2:4]), int(yymmdd[4:6])), side, strike)


def minute_pivot(df, hm_target, expiry):
    snap = df[df["hm"] == hm_target]
    if snap.empty:
        return None
    snap = snap[snap["exp"] == expiry]
    if snap.empty:
        return None
    snap = snap.drop_duplicates(["strike", "side"])
    return snap.pivot(index="strike", columns="side", values=["bid_px_00", "ask_px_00"])


def estimate_spot(piv):
    try:
        cb = piv[("bid_px_00", "C")]; pb = piv[("bid_px_00", "P")]
        common = cb.index.intersection(pb.index)
        common = [k for k in common if pd.notna(cb.loc[k]) and pd.notna(pb.loc[k])]
        if len(common) < 3:
            return None
        cb = cb.loc[common]; pb = pb.loc[common]
        atm = (cb - pb).abs().idxmin()
        return float(cb.loc[atm] - pb.loc[atm] + atm)
    except (KeyError, ValueError):
        return None


def et_hm_in_utc(d, hh, mm):
    """HH:MM string in UTC corresponding to hh:mm ET on date d (DST-correct)."""
    ts = datetime(d.year, d.month, d.day, hh, mm, tzinfo=ET).astimezone(ZoneInfo("UTC"))
    return f"{ts.hour:02d}:{ts.minute:02d}"


def process_day(fp):
    d = datetime.strptime(fp.stem.split("_")[-1], "%Y%m%d").date()
    df = pd.read_parquet(fp, columns=["symbol", "bid_px_00", "ask_px_00"])
    df = df[df["ask_px_00"] > 0].copy()
    if df.empty:
        return d, {}
    parsed = df["symbol"].map(parse)
    df = df[parsed.notna()].copy()
    df["exp"] = parsed[parsed.notna()].map(lambda x: x[0])
    df["side"] = parsed[parsed.notna()].map(lambda x: x[1])
    df["strike"] = parsed[parsed.notna()].map(lambda x: x[2])
    df["hm"] = df.index.strftime("%H:%M")
    e_piv = minute_pivot(df, et_hm_in_utc(d, 9, 45), d)
    x_piv = minute_pivot(df, et_hm_in_utc(d, 15, 55), d)
    if e_piv is None or x_piv is None:
        return d, {}
    spot = estimate_spot(e_piv)
    if spot is None:
        return d, {}
    spot_exit = estimate_spot(x_piv)
    if spot_exit is None:
        spot_exit = spot

    e_idx = e_piv.index
    results = {}
    for otm in OTMS:
        sp_k = round(spot * (1 - otm)); sc_k = round(spot * (1 + otm))
        for wing in WINGS:
            lp_k = sp_k - wing; lc_k = sc_k + wing
            need = [sp_k, lp_k, sc_k, lc_k]
            if not all(k in e_idx for k in need):
                continue
            try:
                legs_entry = [e_piv.loc[sp_k, ("bid_px_00", "P")], e_piv.loc[sc_k, ("bid_px_00", "C")],
                              e_piv.loc[lp_k, ("ask_px_00", "P")], e_piv.loc[lc_k, ("ask_px_00", "C")]]
                if any(pd.isna(v) for v in legs_entry):
                    continue
                sp_b, sc_b, lp_a, lc_a = (float(v) for v in legs_entry)
                cr = (sp_b + sc_b) - (lp_a + lc_a)
                if cr <= 0:
                    continue
                S = spot_exit
                ex = (max(0.0, sp_k - S) + max(0.0, S - sc_k)) - (max(0.0, lp_k - S) + max(0.0, S - lc_k))
                pnl = (cr - ex) * 100
                if pd.isna(pnl):
                    continue
                results[(otm, wing)] = pnl
            except (KeyError, ValueError):
                continue
    return d, results


def main():
    done = set()
    if PARTIAL.exists():
        try:
            done = set(pd.read_csv(PARTIAL)["date"].astype(str))
        except Exception:
            pass
    files = sorted(DATA.glob("iwm_cbbo_*.parquet"))
    print(f"[IWM dstfix] {len(files)} days, {len(done)} already done", flush=True)
    for i, fp in enumerate(files):
        ds = fp.stem.split("_")[-1]
        d0 = f"{ds[:4]}-{ds[4:6]}-{ds[6:8]}"
        if d0 in done:
            continue
        d, res = process_day(fp)
        rows = [{"date": d, "year": d.year, "otm": otm, "wing": wing, "pnl": pnl}
                for (otm, wing), pnl in res.items()]
        if not rows:
            rows = [{"date": d, "year": d.year, "otm": None, "wing": None, "pnl": None}]
        pd.DataFrame(rows).to_csv(PARTIAL, mode="a", header=not PARTIAL.exists(), index=False)
        if i % 100 == 0:
            print(f"  [{i+1}/{len(files)}] {d}", flush=True)
    allr = pd.read_csv(PARTIAL)
    tmp = HERE / ".iwm_dstfix.tmp.csv"
    allr.to_csv(tmp, index=False)
    os.replace(tmp, FINAL)

    allr = allr.dropna(subset=["pnl"])
    print("\nIWM 0DTE IC — DST-FIXED, natural entry, intrinsic 15:55 exit")
    for otm in OTMS:
        for wing in WINGS:
            sub = allr[(allr["otm"] == otm) & (allr["wing"] == wing)]
            if sub.empty:
                continue
            p = sub["pnl"]
            print(f"  OTM={otm*100:.1f}% wing=${wing}: n={len(sub)} total=${p.sum():+,.0f} "
                  f"avg=${p.mean():+.0f} win={(p>0).mean()*100:.0f}% worst=${p.min():+,.0f} "
                  f"| slip-adj -$10/trade: ${p.sum()-10*len(sub):+,.0f}")
        print()
    print("\nPer-year, best config by raw total:")
    allr["cfg"] = allr["otm"].astype(str) + "/" + allr["wing"].astype(str)
    best = allr.groupby("cfg")["pnl"].sum().idxmax()
    sub = allr[allr["cfg"] == best]
    for y, g in sub.groupby("year"):
        p = g["pnl"]
        print(f"  {y}: n={len(g)} total=${p.sum():+,.0f} avg=${p.mean():+.0f} win={(p>0).mean()*100:.0f}% worst=${p.min():+,.0f}")


if __name__ == "__main__":
    main()
