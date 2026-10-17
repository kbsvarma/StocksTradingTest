"""Quick SPX 0DTE BPS backtest: 1% OTM short, 50pt width, hold to expiry, cash settle.

Entry: 09:45 ET (13:45 UTC) using mid-price
Exit:  expiry day SPX close (cash settlement, no slippage on exit)
"""
import re
from datetime import datetime
from pathlib import Path
import pandas as pd

CACHE = Path(__file__).parent
DATA  = CACHE.parent / "narrow_width_databento_2026_05_21" / "spxw_p_cbbo_1m"
SPX   = pd.read_parquet(CACHE.parent / "narrow_width_databento_2026_05_21" / "spot_cache_GSPC.parquet")
VIX   = pd.read_parquet(CACHE / "vix_panel.parquet")

RX_STRIKE = re.compile(r"P0?(\d+)000$")

def parse_strike(s):
    m = RX_STRIKE.search(s.strip())
    return int(m.group(1)) if m else None

def load_at_minute(fp, hh_utc, mm_utc):
    df = pd.read_parquet(fp)
    df = df[(df["bid_px_00"]>0)&(df["ask_px_00"]>0)].copy()
    if df.empty: return None
    # Filter to target minute
    target = f"{hh_utc:02d}:{mm_utc:02d}"
    mask = df.index.strftime("%H:%M") == target
    snap = df[mask]
    if snap.empty:
        # try nearest minute
        return None
    snap = snap.copy()
    snap["strike"] = snap["symbol"].apply(parse_strike)
    snap["mid"] = (snap["bid_px_00"] + snap["ask_px_00"]) / 2
    return snap.dropna(subset=["strike","mid"]).drop_duplicates("strike")

def get_vix(d):
    if d not in VIX.index: return None, None
    vo = float(VIX.loc[d, "vix_open"])
    idx = VIX.index.get_indexer([d], method="ffill")[0]
    if idx < 1: return vo, None
    return vo, float(VIX.iloc[idx-1]["vix_close"])

def settle_bps(spx_close, short_k, long_k):
    """Bull put spread settlement: short_put debit at expiry minus long_put debit."""
    if spx_close >= short_k: return 0.0  # both OTM
    if spx_close >= long_k:  return short_k - spx_close
    return short_k - long_k  # both ITM, max loss = width

def run(width=50, otm_pct=0.01, entry_hh=13, entry_mm=45,
        years=(2024, 2025), vix_filter=True, no_thursday=True):
    rows = []
    files = sorted(DATA.glob("spxw_p_cbbo_*.parquet"))
    for fp in files:
        ds = fp.stem.split("_")[-1]
        try:
            d = datetime.strptime(ds, "%Y%m%d").date()
        except: continue
        if d.year not in years: continue
        if no_thursday and d.weekday() == 3: continue
        ts = pd.Timestamp(d)
        if ts not in SPX.index: continue
        spx_open = float(SPX.loc[ts, "Open"])
        spx_close = float(SPX.loc[ts, "Close"])
        if vix_filter:
            vo, vp = get_vix(ts)
            if vo is None or vp is None: continue
            if vo >= 25 or (vo - vp) >= 1: continue
        snap = load_at_minute(fp, entry_hh, entry_mm)
        if snap is None or snap.empty: continue
        # Target strikes: 1% OTM short, $5 grid
        target_short = round(spx_open * (1 - otm_pct) / 5) * 5
        # snap to existing strike (nearest at or below target)
        strikes = sorted(snap["strike"].unique())
        # short = strike closest to target
        short_k = min(strikes, key=lambda x: abs(x - target_short))
        long_k = short_k - width
        if long_k not in snap["strike"].values: continue
        short_mid = float(snap.set_index("strike").loc[short_k, "mid"])
        long_mid  = float(snap.set_index("strike").loc[long_k, "mid"])
        credit = short_mid - long_mid
        if credit <= 0: continue
        debit_at_exp = settle_bps(spx_close, short_k, long_k)
        pnl = (credit - debit_at_exp) * 100
        rows.append({"date": d, "year": d.year, "dow": d.strftime("%a"),
                     "spx_open": spx_open, "spx_close": spx_close,
                     "short": short_k, "long": long_k,
                     "credit": credit, "exit_settle": debit_at_exp,
                     "pnl": pnl})
    return pd.DataFrame(rows)

def report(df):
    if df.empty:
        print("NO trades"); return
    p = df["pnl"]
    w = p[p>0]; l = p[p<0]
    print(f"\nTotal:  n={len(df)} P&L=${p.sum():+,.0f} avg=${p.mean():+.0f} "
          f"win={(p>0).mean()*100:.1f}% avg_win=${w.mean() if len(w) else 0:+.0f} "
          f"avg_loss=${l.mean() if len(l) else 0:+.0f} worst=${p.min():+,.0f}")
    for y, grp in df.groupby("year"):
        pg = grp["pnl"]
        print(f"  {y}: n={len(grp):3d} total=${pg.sum():+,.0f} avg=${pg.mean():+.0f} "
              f"win={(pg>0).mean()*100:.1f}% worst=${pg.min():+,.0f}")

if __name__ == "__main__":
    import sys
    # entry: 09:45 ET = 13:45 UTC; 15:30 ET = 19:30 UTC; 15:45 ET = 19:45 UTC
    entries = [
        ("09:45 ET", 13, 45),
        ("15:30 ET (30min-close)", 19, 30),
        ("15:45 ET (15min-close)", 19, 45),
    ]
    for otm in [0.005, 0.01, 0.015]:
        print("\n" + "="*100)
        print(f"SPX 0DTE BPS: {otm*100:.1f}% OTM short, 50pt width, hold-to-expiry, filters ON")
        print("="*100)
        for label, hh, mm in entries:
            df = run(width=50, otm_pct=otm, entry_hh=hh, entry_mm=mm,
                     years=(2024,2025), vix_filter=True, no_thursday=True)
            print(f"\n>>> Entry {label}, OTM {otm*100:.1f}%")
            report(df)
