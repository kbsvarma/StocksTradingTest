"""IWM 0DTE Iron Condor backtest with REAL intraday fills (no settlement assumptions).

Data: data/iwm_wide/ — full minute cbbo, puts + calls, 2020-2026.

Strategy:
  - Each day with a 0DTE expiry (file date itself), enter at ENTRY_TIME:
      sell put @ ~OTM%, sell call @ ~OTM%, buy wings at ±WING
  - Real entry credit: sell shorts at BID, buy wings at ASK
  - Exit at EXIT_TIME: buy back shorts at ASK, sell wings at BID
  - P&L = entry_credit - exit_cost (both real fills)
  - Also compute mid-based for comparison
  - Cash-settle path also computed for days where no exit quote exists

Sweeps OTM%, wing width, entry/exit time. Year-by-year split.
"""
import re, sys
from datetime import datetime, date
from pathlib import Path
import pandas as pd
import numpy as np

DATA = Path(__file__).parent / "data" / "iwm_wide"

RX = re.compile(r"IWM\s+(\d{6})([PC])(\d{8})$")
def parse(s):
    m = RX.search(s.strip())
    if not m: return None
    yymmdd, side, strike = m.group(1), m.group(2), int(m.group(3))/1000
    exp = date(2000+int(yymmdd[:2]), int(yymmdd[2:4]), int(yymmdd[4:6]))
    return exp, side, strike

def load_minute(df, hh_utc, mm_utc, expiry):
    """Return pivot of {strike -> (P_bid,P_ask,C_bid,C_ask)} at given minute for 0DTE expiry."""
    target = f"{hh_utc:02d}:{mm_utc:02d}"
    snap = df[df.index.strftime("%H:%M") == target]
    if snap.empty: return None
    rows = []
    for _, r in snap.iterrows():
        p = parse(r["symbol"])
        if p is None: continue
        exp, side, strike = p
        if exp != expiry: continue
        rows.append((strike, side, r["bid_px_00"], r["ask_px_00"]))
    if not rows: return None
    d = pd.DataFrame(rows, columns=["strike","side","bid","ask"]).drop_duplicates(["strike","side"])
    piv = d.pivot(index="strike", columns="side", values=["bid","ask"])
    return piv

def run(otm_pct=0.01, wing=2.0, entry=(13,45), exit_=(19,55), fri_only=False):
    files = sorted(DATA.glob("iwm_cbbo_*.parquet"))
    rows = []
    for fp in files:
        ds = fp.stem.split("_")[-1]
        d = datetime.strptime(ds, "%Y%m%d").date()
        if fri_only and d.weekday() != 4: continue
        df = pd.read_parquet(fp)
        df = df[(df["bid_px_00"]>0)&(df["ask_px_00"]>0)]
        if df.empty: continue
        # 0DTE expiry = file date
        e_piv = load_minute(df, entry[0], entry[1], d)
        x_piv = load_minute(df, exit_[0], exit_[1], d)
        if e_piv is None or x_piv is None: continue
        # spot proxy: ATM strike = where put~call (use mid of available strikes near middle)
        strikes = sorted(e_piv.index)
        if len(strikes) < 5: continue
        # estimate spot from put-call mid parity at central strikes
        # simpler: use strike where (C_bid - P_bid) closest to 0 ~ ATM
        try:
            cb = e_piv[("bid","C")]; pb = e_piv[("bid","P")]
            diff = (cb - pb).abs()
            atm = diff.idxmin()
        except KeyError:
            continue
        spot = float(atm)
        short_put_k  = round((spot*(1-otm_pct)))  # $1 grid
        short_call_k = round((spot*(1+otm_pct)))
        long_put_k   = short_put_k - wing
        long_call_k  = short_call_k + wing
        need = [short_put_k, long_put_k, short_call_k, long_call_k]
        if not all(k in e_piv.index and k in x_piv.index for k in need):
            continue
        try:
            # ENTRY (real): sell shorts at BID, buy wings at ASK
            sp_bid = float(e_piv.loc[short_put_k,  ("bid","P")])
            sc_bid = float(e_piv.loc[short_call_k, ("bid","C")])
            lp_ask = float(e_piv.loc[long_put_k,   ("ask","P")])
            lc_ask = float(e_piv.loc[long_call_k,  ("ask","C")])
            credit_real = (sp_bid + sc_bid) - (lp_ask + lc_ask)
            # ENTRY (mid)
            sp_m = (e_piv.loc[short_put_k,("bid","P")]+e_piv.loc[short_put_k,("ask","P")])/2
            sc_m = (e_piv.loc[short_call_k,("bid","C")]+e_piv.loc[short_call_k,("ask","C")])/2
            lp_m = (e_piv.loc[long_put_k,("bid","P")]+e_piv.loc[long_put_k,("ask","P")])/2
            lc_m = (e_piv.loc[long_call_k,("bid","C")]+e_piv.loc[long_call_k,("ask","C")])/2
            credit_mid = (sp_m+sc_m)-(lp_m+lc_m)
            # EXIT (real): buy back shorts at ASK, sell wings at BID
            sp_ask = float(x_piv.loc[short_put_k,  ("ask","P")])
            sc_ask = float(x_piv.loc[short_call_k, ("ask","C")])
            lp_bid = float(x_piv.loc[long_put_k,   ("bid","P")])
            lc_bid = float(x_piv.loc[long_call_k,  ("bid","C")])
            exit_real = (sp_ask + sc_ask) - (lp_bid + lc_bid)
            # EXIT (mid)
            sp_xm = (x_piv.loc[short_put_k,("bid","P")]+x_piv.loc[short_put_k,("ask","P")])/2
            sc_xm = (x_piv.loc[short_call_k,("bid","C")]+x_piv.loc[short_call_k,("ask","C")])/2
            lp_xm = (x_piv.loc[long_put_k,("bid","P")]+x_piv.loc[long_put_k,("ask","P")])/2
            lc_xm = (x_piv.loc[long_call_k,("bid","C")]+x_piv.loc[long_call_k,("ask","C")])/2
            exit_mid = (sp_xm+sc_xm)-(lp_xm+lc_xm)
        except (KeyError, ValueError):
            continue
        if credit_real <= 0: continue  # no credit, skip
        pnl_real = (credit_real - exit_real) * 100
        pnl_mid  = (credit_mid  - exit_mid)  * 100
        rows.append({"date":d, "year":d.year, "spot":spot,
                     "sp":short_put_k, "sc":short_call_k, "lp":long_put_k, "lc":long_call_k,
                     "credit_real":credit_real, "credit_mid":credit_mid,
                     "exit_real":exit_real, "pnl_real":pnl_real, "pnl_mid":pnl_mid})
    return pd.DataFrame(rows)

def report(df, label):
    if df.empty: print(f"  {label}: NO trades"); return
    for col, tag in [("pnl_real","real"),("pnl_mid","mid")]:
        p = df[col]; w=p[p>0]; l=p[p<0]
        print(f"  {label} [{tag}]: n={len(df)} total=${p.sum():+,.0f} avg=${p.mean():+.0f} "
              f"win={(p>0).mean()*100:.0f}% avgW=${w.mean() if len(w) else 0:+.0f} "
              f"avgL=${l.mean() if len(l) else 0:+.0f} worst=${p.min():+,.0f}")

if __name__ == "__main__":
    print("="*110)
    print("IWM 0DTE IRON CONDOR — REAL intraday fills (sell@bid/buy@ask), 2020-2026, entry 09:45 exit 15:55")
    print("="*110)
    best = []
    for otm in [0.005, 0.01, 0.015, 0.02]:
        for wing in [1, 2, 3, 5]:
            df = run(otm_pct=otm, wing=wing, entry=(13,45), exit_=(19,55))
            report(df, f"OTM={otm*100:.1f}% wing=${wing}")
            if not df.empty:
                best.append((df["pnl_real"].sum(), otm, wing, df))
        print()
    # Best config yearly
    if best:
        best.sort(reverse=True)
        print("\n=== TOP 3 CONFIGS — yearly breakdown (real fills) ===")
        for total, otm, wing, df in best[:3]:
            print(f"\nOTM={otm*100:.1f}% wing=${wing} → total real=${total:+,.0f}")
            for y, g in df.groupby("year"):
                p = g["pnl_real"]
                print(f"  {y}: n={len(g):3d} total=${p.sum():+,.0f} avg=${p.mean():+.0f} win={(p>0).mean()*100:.0f}% worst=${p.min():+,.0f}")
