"""2-7 DTE same-day intraday backtest.

Entry 09:45 ET: build PUT credit spread at chosen DTE/credit target.
Exit 15:55 ET same day: close at mid using market data.
NoThu filter + VIX gate (vix_open<25 AND vix_gap<1) to match SPX winner.
"""
import re
from datetime import datetime
from pathlib import Path
import pandas as pd

CACHE = Path(__file__).parent
DATA = CACHE / "short_dte_intraday_data"
VIX = pd.read_parquet(CACHE / "vix_panel.parquet")

RX = re.compile(r"P0?(\d+)000$")
def parse_strike(s):
    m = RX.search(s.strip()); return int(m.group(1)) if m else None
def parse_expiry(s):
    m = re.search(r"(\d{6})P", s.strip())
    return datetime.strptime(m.group(1), "%y%m%d").date() if m else None

def load_day(fp, entry_date):
    df = pd.read_parquet(fp)
    df = df[(df["bid_px_00"]>0) & (df["ask_px_00"]>0)].copy()
    if df.empty: return None, None
    df["strike"] = df["symbol"].apply(parse_strike)
    df["expiration"] = df["symbol"].apply(parse_expiry)
    df["mid"] = (df["bid_px_00"] + df["ask_px_00"]) / 2
    df = df.dropna(subset=["strike","expiration","mid"])
    df["dte"] = (df["expiration"] - entry_date).apply(lambda x: x.days)
    entry = df[df["snapshot"]=="entry"][["strike","expiration","dte","mid","bid_px_00","ask_px_00"]]
    exit_  = df[df["snapshot"]=="exit"][["strike","expiration","mid","bid_px_00","ask_px_00"]]
    # de-dupe in case of multiple rows
    entry = entry.drop_duplicates(subset=["strike","expiration"])
    exit_ = exit_.drop_duplicates(subset=["strike","expiration"])
    return entry, exit_

def get_vix(entry_date):
    d = pd.Timestamp(entry_date)
    if d not in VIX.index: return None, None
    vo = float(VIX.loc[d, "vix_open"])
    idx = VIX.index.get_indexer([d], method="ffill")[0]
    if idx < 1: return vo, None
    vp = float(VIX.iloc[idx-1]["vix_close"])
    return vo, vp

def find_spread(entry_df, dte_lo, dte_hi, target_cred, min_cred, width):
    cands = entry_df[(entry_df["dte"]>=dte_lo) & (entry_df["dte"]<=dte_hi)]
    if cands.empty: return None
    best = None; bestd = float("inf")
    for exp, grp in cands.groupby("expiration"):
        grp = grp.set_index("strike").sort_index()
        for sk in grp.index:
            lk = sk - width
            if lk not in grp.index: continue
            cred = grp.loc[sk,"mid"] - grp.loc[lk,"mid"]
            if cred < min_cred: continue
            d = abs(cred - target_cred)
            if d < bestd:
                bestd = d
                best = {"expiration":exp, "dte":int(grp.loc[sk,"dte"]),
                        "short":int(sk), "long":int(lk),
                        "entry_credit": float(cred),
                        "short_bid": float(grp.loc[sk,"bid_px_00"]),
                        "short_ask": float(grp.loc[sk,"ask_px_00"]),
                        "long_bid":  float(grp.loc[lk,"bid_px_00"]),
                        "long_ask":  float(grp.loc[lk,"ask_px_00"])}
    return best

def exit_price(exit_df, short, long_, expiration):
    """Close = buy short, sell long. Cost = short_ask - long_bid (worst), or mids."""
    e = exit_df[exit_df["expiration"]==expiration].set_index("strike")
    if short not in e.index or long_ not in e.index: return None, None
    short_mid = float(e.loc[short,"mid"]); long_mid = float(e.loc[long_,"mid"])
    short_ask = float(e.loc[short,"ask_px_00"]); long_bid = float(e.loc[long_,"bid_px_00"])
    return short_mid - long_mid, short_ask - long_bid

def run(target_dte, target_cred_pct, width=50, min_cred_pct=0.05, vix_filter=True):
    cred_t = width*target_cred_pct
    cred_m = width*min_cred_pct
    dte_lo, dte_hi = max(1,target_dte-1), target_dte+2
    rows = []
    for fp in sorted(DATA.glob("short_dte_*.parquet")):
        ds = fp.stem.split("_")[-1]
        entry = datetime.strptime(ds,"%Y%m%d").date()
        if entry.weekday()==3: continue  # noThu
        if vix_filter:
            vo, vp = get_vix(entry)
            if vo is None or vp is None: continue
            if vo>=25 or (vo-vp)>=1: continue
        ed, xd = load_day(fp, entry)
        if ed is None or xd is None or ed.empty or xd.empty: continue
        sp = find_spread(ed, dte_lo, dte_hi, cred_t, cred_m, width)
        if sp is None: continue
        cost_mid, cost_real = exit_price(xd, sp["short"], sp["long"], sp["expiration"])
        if cost_mid is None: continue
        pnl_mid  = (sp["entry_credit"] - cost_mid)  * 100
        pnl_real = (sp["entry_credit"] - cost_real) * 100
        rows.append({"entry":entry,"dte":sp["dte"],"short":sp["short"],"long":sp["long"],
                     "credit":sp["entry_credit"],"exit_mid":cost_mid,"exit_real":cost_real,
                     "pnl_mid":pnl_mid,"pnl_real":pnl_real})
    return pd.DataFrame(rows)

def summary(df, label):
    if df.empty:
        print(f"  {label}: NO trades"); return
    for col, tag in [("pnl_mid","mid"), ("pnl_real","real")]:
        p = df[col]
        w = p[p>0]; l = p[p<0]
        print(f"  {label} [{tag}]: n={len(df)} total=${p.sum():+,.0f} avg=${p.mean():+.0f} "
              f"win={(p>0).mean()*100:.0f}% avg_win=${w.mean() if len(w) else 0:+.0f} "
              f"avg_loss=${l.mean() if len(l) else 0:+.0f} worst=${p.min():+,.0f}")

if __name__ == "__main__":
    import sys
    print("="*100)
    print("2-7 DTE INTRADAY — width sweep (PUT credit spread, 09:45→15:55, noThu, vix gate)")
    print("="*100); print()
    widths = [50, 100, 150, 200] if "--wide" in sys.argv else [50]
    for w in widths:
        print(f"\n### WIDTH = {w}pt ###")
        for dte in [2,3,5,7]:
            for pct in [0.10, 0.20, 0.30, 0.40]:
                df = run(target_dte=dte, target_cred_pct=pct, width=w)
                summary(df, f"DTE={dte} cred={int(pct*100)}% w={w}")
            print()
