"""Long-put crash hedge: buy 2-7 DTE OTM put at 09:45, close 15:55.

Negative theta on quiet days, payoff on crash days. Test if EV is positive
or if it pairs well with legacy BPS bot.
"""
import re
from datetime import datetime
from pathlib import Path
import pandas as pd

CACHE = Path(__file__).parent
DATA = CACHE / "short_dte_intraday_data"
VIX = pd.read_parquet(CACHE / "vix_panel.parquet")
SPX = pd.read_parquet(CACHE.parent / "narrow_width_databento_2026_05_21" / "spot_cache_GSPC.parquet")

RX = re.compile(r"P0?(\d+)000$")
def parse_strike(s):
    m = RX.search(s.strip()); return int(m.group(1)) if m else None
def parse_expiry(s):
    m = re.search(r"(\d{6})P", s.strip())
    return datetime.strptime(m.group(1), "%y%m%d").date() if m else None

def load(fp, ed):
    df = pd.read_parquet(fp)
    df = df[(df["bid_px_00"]>0) & (df["ask_px_00"]>0)].copy()
    if df.empty: return None, None
    df["strike"] = df["symbol"].apply(parse_strike)
    df["expiration"] = df["symbol"].apply(parse_expiry)
    df["mid"] = (df["bid_px_00"] + df["ask_px_00"]) / 2
    df = df.dropna(subset=["strike","expiration","mid"])
    df["dte"] = (df["expiration"] - ed).apply(lambda x: x.days)
    e = df[df["snapshot"]=="entry"][["strike","expiration","dte","mid","bid_px_00","ask_px_00"]]
    x = df[df["snapshot"]=="exit"][["strike","expiration","mid","bid_px_00","ask_px_00"]]
    return e.drop_duplicates(["strike","expiration"]), x.drop_duplicates(["strike","expiration"])

def run(target_dte, otm_pct, max_premium=None):
    """Buy a put OTM by ~otm_pct of SPX. Exit at 15:55.

    Pay ask on entry, receive bid on exit (realistic).
    """
    dte_lo, dte_hi = max(1, target_dte-1), target_dte+2
    rows = []
    for fp in sorted(DATA.glob("short_dte_*.parquet")):
        ds = fp.stem.split("_")[-1]
        ed = datetime.strptime(ds, "%Y%m%d").date()
        d = pd.Timestamp(ed)
        if d not in SPX.index: continue
        spx_open = float(SPX.loc[d, "Open"])
        target_strike = round(spx_open * (1 - otm_pct) / 25) * 25
        edf, xdf = load(fp, ed)
        if edf is None or xdf is None or edf.empty or xdf.empty: continue
        # pick strike in DTE band closest to target
        cands = edf[(edf["dte"]>=dte_lo) & (edf["dte"]<=dte_hi)]
        if cands.empty: continue
        # nearest expiration to target_dte
        cands = cands.copy()
        cands["distance"] = (cands["strike"] - target_strike).abs() + (cands["dte"] - target_dte).abs() * 5
        idx = cands["distance"].idxmin()
        row = cands.loc[idx] if not isinstance(cands.loc[idx], pd.DataFrame) else cands.loc[idx].iloc[0]
        if isinstance(row, pd.DataFrame): row = row.iloc[0]
        entry_ask = float(row["ask_px_00"])
        if max_premium is not None and entry_ask > max_premium: continue
        # exit
        ex = xdf[(xdf["strike"]==row["strike"]) & (xdf["expiration"]==row["expiration"])]
        if ex.empty: continue
        exit_bid = float(ex.iloc[0]["bid_px_00"])
        pnl_real = (exit_bid - entry_ask) * 100
        pnl_mid  = (float(ex.iloc[0]["mid"]) - float(row["mid"])) * 100
        rows.append({"entry":ed, "dte":int(row["dte"]), "strike":int(row["strike"]),
                     "entry_ask":entry_ask, "exit_bid":exit_bid,
                     "pnl_real":pnl_real, "pnl_mid":pnl_mid,
                     "spx_open":spx_open})
    return pd.DataFrame(rows)

def summary(df, label):
    if df.empty:
        print(f"  {label}: NO trades"); return
    p = df["pnl_real"]
    w = p[p>0]; l = p[p<0]
    big_win = p[p>500]
    print(f"  {label}: n={len(df)} total=${p.sum():+,.0f} avg=${p.mean():+.0f} "
          f"win={(p>0).mean()*100:.0f}% avg_win=${w.mean() if len(w) else 0:+.0f} "
          f"avg_loss=${l.mean() if len(l) else 0:+.0f} "
          f"best=${p.max():+,.0f} (>$500 wins: {len(big_win)})")

if __name__ == "__main__":
    print("="*100)
    print("LONG PUT CRASH HEDGE (buy 09:45 OTM put, sell 15:55) — real fills")
    print("="*100); print()
    for dte in [2, 3, 5, 7]:
        for otm in [0.005, 0.01, 0.02, 0.03]:
            df = run(target_dte=dte, otm_pct=otm, max_premium=10.0)
            summary(df, f"DTE={dte} OTM={otm*100:.1f}% maxprem=$10")
        print()
