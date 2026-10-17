"""AVGO Pin-IF backtest on M/W/F 2026 0DTE data.
Uses BOTH put and call cbbo (no parity needed)."""
import re
from datetime import datetime, date
from pathlib import Path
import pandas as pd

CACHE = Path(__file__).parent
OI_DIR = CACHE / "pin_oi_data"
AVGO_DIR = CACHE.parent / "avgo_ic_2025" / "data"

RX_STRIKE = re.compile(r"([PC])(\d{8})$")
RX_EXP = re.compile(r"^([A-Z]+)\s+(\d{6})[PC]")

def parse_sym(s):
    s = s.strip()
    m1 = RX_STRIKE.search(s); m2 = RX_EXP.match(s)
    if not m1 or not m2: return None
    return int(m1.group(2))/1000, m1.group(1), date(2000+int(m2.group(2)[:2]), int(m2.group(2)[2:4]), int(m2.group(2)[4:6]))

def load_oi(expiry_date):
    fp = OI_DIR / f"avgo_oi_{expiry_date.strftime('%Y%m%d')}.parquet"
    if not fp.exists(): return None
    df = pd.read_parquet(fp)
    parsed = df["symbol"].apply(parse_sym)
    df = df[parsed.notna()].copy()
    df["strike"] = parsed[parsed.notna()].apply(lambda x: x[0])
    df["side"]   = parsed[parsed.notna()].apply(lambda x: x[1])
    df["expiration"] = parsed[parsed.notna()].apply(lambda x: x[2])
    df = df[df["expiration"]==expiry_date].copy()
    df["oi"] = df["quantity"].astype(float)
    return df[["strike","side","oi"]].drop_duplicates(["strike","side"])

def pin_combined(oi, spot):
    if oi is None or oi.empty: return None
    agg = oi.groupby("strike")["oi"].sum().reset_index()
    agg = agg[(agg["strike"] >= spot*0.95) & (agg["strike"] <= spot*1.05)]
    if agg.empty: return None
    return float(agg.loc[agg["oi"].idxmax(), "strike"])

def pin_gamma_flip(oi, spot):
    if oi is None or oi.empty: return None
    g = oi.groupby(["strike","side"])["oi"].sum().unstack(fill_value=0).reset_index()
    if "C" not in g or "P" not in g: return None
    g = g.sort_values("strike").reset_index(drop=True)
    g["cum_put"] = g["P"].cumsum()
    g["cum_call_above"] = g["C"][::-1].cumsum()[::-1]
    g["flip_score"] = (g["cum_call_above"] - g["cum_put"]).abs()
    g = g[(g["strike"] >= spot*0.95) & (g["strike"] <= spot*1.05)]
    if g.empty: return None
    return float(g.loc[g["flip_score"].idxmin(), "strike"])

def load_avgo_minute(expiry_date, hh_utc, mm_utc):
    fp = AVGO_DIR / f"avgo_cbbo_{expiry_date.strftime('%Y%m%d')}.parquet"
    if not fp.exists(): return None
    df = pd.read_parquet(fp)
    df = df[(df["bid_px_00"]>0) & (df["ask_px_00"]>0)].copy()
    if df.empty: return None
    target = f"{hh_utc:02d}:{mm_utc:02d}"
    snap = df[df.index.strftime("%H:%M") == target].copy()
    if snap.empty: return None
    parsed = snap["symbol"].apply(parse_sym)
    snap = snap[parsed.notna()].copy()
    snap["strike"] = parsed[parsed.notna()].apply(lambda x: x[0])
    snap["side"]   = parsed[parsed.notna()].apply(lambda x: x[1])
    snap["expiration"] = parsed[parsed.notna()].apply(lambda x: x[2])
    snap = snap[snap["expiration"]==expiry_date].copy()
    snap["mid"] = (snap["bid_px_00"]+snap["ask_px_00"])/2
    return snap[["strike","side","mid","bid_px_00","ask_px_00"]].drop_duplicates(["strike","side"])

def get_avgo_spot(expiry_date):
    """Approximate AVGO spot from ATM straddle parity at entry."""
    snap = load_avgo_minute(expiry_date, 13, 45)
    if snap is None or snap.empty: return None
    # Find strike with smallest call-put difference (=ATM)
    pivot = snap.pivot_table(index="strike", columns="side", values="mid", aggfunc="first")
    if "C" not in pivot or "P" not in pivot: return None
    pivot = pivot.dropna()
    if pivot.empty: return None
    pivot["pc_diff"] = (pivot["C"] - pivot["P"]).abs()
    atm = pivot["pc_diff"].idxmin()
    # parity: C - P = S - K → S = C - P + K
    return float(pivot.loc[atm, "C"] - pivot.loc[atm, "P"] + atm)

def trade_avgo_if(expiry_date, center, wing, entry_hh=13, entry_mm=45, exit_hh=19, exit_mm=55):
    e = load_avgo_minute(expiry_date, entry_hh, entry_mm)
    x = load_avgo_minute(expiry_date, exit_hh, exit_mm)
    if e is None or x is None: return None
    e_piv = e.pivot_table(index="strike", columns="side", values="mid", aggfunc="first")
    x_piv = x.pivot_table(index="strike", columns="side", values="mid", aggfunc="first")
    # Snap center to nearest available strike
    avail = sorted(set(e_piv.index) & set(x_piv.index))
    if not avail: return None
    short_k = min(avail, key=lambda v: abs(v - center))
    long_call_k = short_k + wing
    long_put_k  = short_k - wing
    # Snap wings to nearest available
    if long_call_k not in avail:
        cand = [a for a in avail if a > short_k]
        if not cand: return None
        long_call_k = min(cand, key=lambda v: abs(v - long_call_k))
    if long_put_k not in avail:
        cand = [a for a in avail if a < short_k]
        if not cand: return None
        long_put_k = min(cand, key=lambda v: abs(v - long_put_k))
    try:
        sp_entry = float(e_piv.loc[short_k, "P"]); sc_entry = float(e_piv.loc[short_k, "C"])
        lp_entry = float(e_piv.loc[long_put_k, "P"]); lc_entry = float(e_piv.loc[long_call_k, "C"])
        sp_exit  = float(x_piv.loc[short_k, "P"]); sc_exit  = float(x_piv.loc[short_k, "C"])
        lp_exit  = float(x_piv.loc[long_put_k, "P"]); lc_exit  = float(x_piv.loc[long_call_k, "C"])
    except KeyError:
        return None
    if any(pd.isna(v) for v in [sp_entry, sc_entry, lp_entry, lc_entry, sp_exit, sc_exit, lp_exit, lc_exit]):
        return None
    credit = (sp_entry + sc_entry) - (lp_entry + lc_entry)
    exit_cost = (sp_exit + sc_exit) - (lp_exit + lc_exit)
    pnl_mid = (credit - exit_cost) * 100
    # Realistic fills: pay ask on buy-back, receive bid on close-shorts → use real bid/ask at exit
    try:
        sp_exit_ask = float(x[(x["strike"]==short_k) & (x["side"]=="P")]["ask_px_00"].iloc[0])
        sc_exit_ask = float(x[(x["strike"]==short_k) & (x["side"]=="C")]["ask_px_00"].iloc[0])
        lp_exit_bid = float(x[(x["strike"]==long_put_k) & (x["side"]=="P")]["bid_px_00"].iloc[0])
        lc_exit_bid = float(x[(x["strike"]==long_call_k) & (x["side"]=="C")]["bid_px_00"].iloc[0])
        exit_real = (sp_exit_ask + sc_exit_ask) - (lp_exit_bid + lc_exit_bid)
        pnl_real = (credit - exit_real) * 100
    except (KeyError, IndexError):
        pnl_real = pnl_mid
    return {"date":expiry_date, "short":short_k, "wing":wing, "credit":credit,
            "pnl_mid":pnl_mid, "pnl_real":pnl_real}

def run(wing, definition):
    rows = []
    files = sorted(OI_DIR.glob("avgo_oi_*.parquet"))
    for fp in files:
        ds = fp.stem.split("_")[-1]
        expiry = datetime.strptime(ds, "%Y%m%d").date()
        oi = load_oi(expiry)
        if oi is None: continue
        spot = get_avgo_spot(expiry)
        if spot is None: continue
        if definition == "combined":
            center = pin_combined(oi, spot)
        elif definition == "gamma_flip":
            center = pin_gamma_flip(oi, spot)
        elif definition == "atm_control":
            center = spot
        else:
            continue
        if center is None: continue
        t = trade_avgo_if(expiry, center, wing)
        if t is None: continue
        t["definition"] = definition
        t["center"] = center
        t["spot"]   = spot
        rows.append(t)
    return pd.DataFrame(rows)

def report(df, label):
    if df.empty: print(f"  {label}: NO trades"); return
    for col, tag in [("pnl_mid","mid"), ("pnl_real","real")]:
        p = df[col]; w = p[p>0]; l = p[p<0]
        print(f"  {label} [{tag}]: n={len(df)} total=${p.sum():+,.0f} avg=${p.mean():+.0f} "
              f"win={(p>0).mean()*100:.0f}% avg_win=${w.mean() if len(w) else 0:+.0f} "
              f"avg_loss=${l.mean() if len(l) else 0:+.0f} worst=${p.min():+,.0f}")

if __name__ == "__main__":
    print("="*110)
    print("AVGO 0DTE PIN-RISK IRON FLY BACKTEST — M/W/F 2026, entry 09:45 → exit 15:55 ET")
    print("="*110)
    for wing in [5, 10, 15, 25]:
        print(f"\n### Wing = ${wing} ###")
        for defn in ["atm_control", "combined", "gamma_flip"]:
            df = run(wing, defn)
            report(df, f"{defn:12s} wing=${wing}")
        print()
