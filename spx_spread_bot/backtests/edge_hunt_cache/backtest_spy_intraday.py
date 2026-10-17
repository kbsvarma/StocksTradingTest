"""SPY 2-7 DTE intraday BPS backtest. SPY uses $1 grid + smaller widths.
Multiplier is $100/contract same as index options but each strike is $1.
"""
import re
from datetime import datetime
from pathlib import Path
import pandas as pd

CACHE = Path(__file__).parent
DATA = CACHE / "spy_short_dte_data"
VIX = pd.read_parquet(CACHE / "vix_panel.parquet")

# SPY symbol format: "SPY   YYMMDDP00500000"
RX_S = re.compile(r"P(\d{8})$")
RX_E = re.compile(r"SPY\s+(\d{6})P")

def parse_strike(s):
    m = RX_S.search(s.strip()); return int(m.group(1))/1000 if m else None
def parse_expiry(s):
    m = RX_E.search(s.strip())
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

def get_vix(ed):
    d = pd.Timestamp(ed)
    if d not in VIX.index: return None, None
    vo = float(VIX.loc[d, "vix_open"])
    idx = VIX.index.get_indexer([d], method="ffill")[0]
    if idx < 1: return vo, None
    return vo, float(VIX.iloc[idx-1]["vix_close"])

def find_spread(edf, dte_lo, dte_hi, target_cred, min_cred, width):
    cands = edf[(edf["dte"]>=dte_lo) & (edf["dte"]<=dte_hi)]
    if cands.empty: return None
    best = None; bestd = float("inf")
    for exp, grp in cands.groupby("expiration"):
        g = grp.set_index("strike").sort_index()
        for sk in g.index:
            lk = sk - width
            if lk not in g.index: continue
            cred = g.loc[sk,"mid"] - g.loc[lk,"mid"]
            if cred < min_cred: continue
            d = abs(cred - target_cred)
            if d < bestd:
                bestd = d
                best = {"expiration":exp, "dte":int(g.loc[sk,"dte"]),
                        "short":sk, "long":lk, "credit":float(cred)}
    return best

def exit_cost(xdf, short, long_, exp):
    e = xdf[xdf["expiration"]==exp].set_index("strike")
    if short not in e.index or long_ not in e.index: return None, None
    sm = float(e.loc[short,"mid"]); lm = float(e.loc[long_,"mid"])
    sa = float(e.loc[short,"ask_px_00"]); lb = float(e.loc[long_,"bid_px_00"])
    return sm - lm, sa - lb

def run(target_dte, target_cred_pct, width, min_cred_pct=0.05, vix_filter=True):
    cred_t = width * target_cred_pct
    cred_m = width * min_cred_pct
    dte_lo, dte_hi = max(1, target_dte-1), target_dte+2
    rows = []
    for fp in sorted(DATA.glob("spy_short_dte_*.parquet")):
        ds = fp.stem.split("_")[-1]
        ed = datetime.strptime(ds, "%Y%m%d").date()
        if ed.weekday() == 3: continue
        if vix_filter:
            vo, vp = get_vix(ed)
            if vo is None or vp is None: continue
            if vo>=25 or (vo-vp)>=1: continue
        edf, xdf = load(fp, ed)
        if edf is None or xdf is None or edf.empty or xdf.empty: continue
        sp = find_spread(edf, dte_lo, dte_hi, cred_t, cred_m, width)
        if sp is None: continue
        cm, cr = exit_cost(xdf, sp["short"], sp["long"], sp["expiration"])
        if cm is None: continue
        pnl_mid  = (sp["credit"] - cm) * 100
        pnl_real = (sp["credit"] - cr) * 100
        rows.append({"entry":ed, "dte":sp["dte"], "short":sp["short"], "long":sp["long"],
                     "credit":sp["credit"], "exit_real":cr,
                     "pnl_mid":pnl_mid, "pnl_real":pnl_real})
    return pd.DataFrame(rows)

def summary(df, label):
    if df.empty:
        print(f"  {label}: NO trades"); return
    for col, tag in [("pnl_mid","mid"), ("pnl_real","real")]:
        p = df[col]; w = p[p>0]; l = p[p<0]
        print(f"  {label} [{tag}]: n={len(df)} total=${p.sum():+,.0f} avg=${p.mean():+.0f} "
              f"win={(p>0).mean()*100:.0f}% avg_win=${w.mean() if len(w) else 0:+.0f} "
              f"avg_loss=${l.mean() if len(l) else 0:+.0f} worst=${p.min():+,.0f}")

if __name__ == "__main__":
    print("="*100)
    print("SPY 2-7 DTE intraday PUT credit spread (09:45 → 15:55)")
    print("="*100); print()
    # SPY widths in $: 5, 10, 15, 20 (= SPX 50, 100, 150, 200 since SPY≈SPX/10)
    for w in [5, 10, 15, 20]:
        print(f"\n### WIDTH = ${w} (SPX-equiv {w*10}pt) ###")
        for dte in [2, 3, 5, 7]:
            for pct in [0.10, 0.20, 0.30]:
                df = run(target_dte=dte, target_cred_pct=pct, width=w)
                summary(df, f"DTE={dte} cred={int(pct*100)}% w=${w}")
            print()
