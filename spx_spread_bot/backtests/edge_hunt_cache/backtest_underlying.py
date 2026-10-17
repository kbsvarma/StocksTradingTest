"""Generic 2-7 DTE intraday BPS backtest for any underlying.

Usage: python backtest_underlying.py --ticker TLT --widths 1,2,3,5
"""
import re, argparse
from datetime import datetime
from pathlib import Path
import pandas as pd

CACHE = Path(__file__).parent
VIX = pd.read_parquet(CACHE / "vix_panel.parquet")

def make_parsers(ticker):
    root6 = ticker.ljust(6)
    rx_strike = re.compile(r"P(\d{8})$")
    rx_exp = re.compile(re.escape(root6) + r"(\d{6})P")
    def ps(s):
        m = rx_strike.search(s.strip()); return int(m.group(1))/1000 if m else None
    def pe(s):
        m = rx_exp.search(s.strip())
        return datetime.strptime(m.group(1), "%y%m%d").date() if m else None
    return ps, pe

def load(fp, ed, parse_strike, parse_expiry):
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
            lk = round(sk - width, 2)
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

def run(ticker, target_dte, target_cred_pct, width, vix_filter=True):
    parse_strike, parse_expiry = make_parsers(ticker.upper())
    data_dir = CACHE / f"{ticker.lower()}_short_dte_data"
    cred_t = width * target_cred_pct
    cred_m = width * 0.03
    dte_lo, dte_hi = max(1, target_dte-1), target_dte+2
    rows = []
    for fp in sorted(data_dir.glob(f"{ticker.lower()}_short_dte_*.parquet")):
        ds = fp.stem.split("_")[-1]
        ed = datetime.strptime(ds, "%Y%m%d").date()
        if ed.weekday() == 3: continue
        if vix_filter:
            vo, vp = get_vix(ed)
            if vo is None or vp is None: continue
            if vo>=25 or (vo-vp)>=1: continue
        edf, xdf = load(fp, ed, parse_strike, parse_expiry)
        if edf is None or xdf is None or edf.empty or xdf.empty: continue
        sp = find_spread(edf, dte_lo, dte_hi, cred_t, cred_m, width)
        if sp is None: continue
        cm, cr = exit_cost(xdf, sp["short"], sp["long"], sp["expiration"])
        if cm is None: continue
        rows.append({"entry":ed, "dte":sp["dte"], "short":sp["short"], "long":sp["long"],
                     "credit":sp["credit"], "exit_real":cr,
                     "pnl_mid":(sp["credit"]-cm)*100, "pnl_real":(sp["credit"]-cr)*100})
    return pd.DataFrame(rows)

def summary(df, label):
    if df.empty:
        print(f"  {label}: NO trades"); return
    for col, tag in [("pnl_real","real")]:
        p = df[col]; w = p[p>0]; l = p[p<0]
        print(f"  {label}: n={len(df)} total=${p.sum():+,.0f} avg=${p.mean():+.0f} "
              f"win={(p>0).mean()*100:.0f}% avg_win=${w.mean() if len(w) else 0:+.0f} "
              f"avg_loss=${l.mean() if len(l) else 0:+.0f} worst=${p.min():+,.0f}")

if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--ticker", required=True)
    p.add_argument("--widths", default="1,2,3,5,10")
    p.add_argument("--years", action="store_true", help="show yearly breakdown")
    args = p.parse_args()

    widths = [float(w) for w in args.widths.split(",")]
    print("="*110)
    print(f"{args.ticker.upper()} 2-7 DTE intraday PUT credit spread (09:45→15:55, noThu, vix gate)")
    print("="*110)
    best_results = []
    for w in widths:
        print(f"\n### {args.ticker.upper()} WIDTH=${w} ###")
        for dte in [2, 3, 5, 7]:
            for pct in [0.10, 0.20, 0.30]:
                df = run(args.ticker, target_dte=dte, target_cred_pct=pct, width=w)
                summary(df, f"DTE={dte} cred={int(pct*100)}% w=${w}")
                if not df.empty:
                    best_results.append((df["pnl_real"].sum(), args.ticker, dte, pct, w, df))
            print()

    # Top configs yearly
    if args.years and best_results:
        best_results.sort(reverse=True)
        print(f"\n=== TOP 3 {args.ticker.upper()} CONFIGS — yearly breakdown ===")
        for total, t, dte, pct, w, df in best_results[:3]:
            df["entry"] = pd.to_datetime(df["entry"])
            df["year"] = df["entry"].dt.year
            print(f"\nDTE={dte} cred={int(pct*100)}% w=${w} → total=${total:+,.0f}")
            for y, grp in df.groupby("year"):
                p = grp["pnl_real"]
                print(f"  {y}: n={len(grp):3d} total=${p.sum():+,.0f} avg=${p.mean():+.0f} "
                      f"win={(p>0).mean()*100:.0f}% worst=${p.min():+,.0f}")
