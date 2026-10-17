"""Iron Condor / Iron Fly intraday backtest on 2-7 DTE.

Entry 09:45: sell put spread + sell call spread (IC), or both ATM (IF).
Exit 15:55: close all 4 legs at mid/real prices.

Loads PUT data from short_dte_intraday_data/ and CALL data from short_dte_calls_data/.
"""
import re
from datetime import datetime
from pathlib import Path
import pandas as pd

CACHE = Path(__file__).parent
PUT_DIR = CACHE / "short_dte_intraday_data"
CALL_DIR = CACHE / "short_dte_calls_data"
VIX = pd.read_parquet(CACHE / "vix_panel.parquet")
SPX = pd.read_parquet(CACHE.parent / "narrow_width_databento_2026_05_21" / "spot_cache_GSPC.parquet")

RX_STRIKE = re.compile(r"[PC]0?(\d+)000$")
RX_EXP = re.compile(r"(\d{6})[PC]")

def parse_strike(s):
    m = RX_STRIKE.search(s.strip()); return int(m.group(1)) if m else None
def parse_expiry(s):
    m = RX_EXP.search(s.strip())
    return datetime.strptime(m.group(1), "%y%m%d").date() if m else None

def load_side(fp, entry_date):
    df = pd.read_parquet(fp)
    df = df[(df["bid_px_00"]>0) & (df["ask_px_00"]>0)].copy()
    if df.empty: return None, None
    df["strike"] = df["symbol"].apply(parse_strike)
    df["expiration"] = df["symbol"].apply(parse_expiry)
    df["mid"] = (df["bid_px_00"] + df["ask_px_00"]) / 2
    df = df.dropna(subset=["strike","expiration","mid"])
    df["dte"] = (df["expiration"] - entry_date).apply(lambda x: x.days)
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

def find_spread(entry_df, dte_lo, dte_hi, target_cred, min_cred, width, is_put):
    """For PUT: short higher strike, long lower. For CALL: short lower, long higher."""
    cands = entry_df[(entry_df["dte"]>=dte_lo) & (entry_df["dte"]<=dte_hi)]
    if cands.empty: return None
    best = None; bestd = float("inf")
    for exp, grp in cands.groupby("expiration"):
        g = grp.set_index("strike").sort_index()
        for sk in g.index:
            lk = sk - width if is_put else sk + width
            if lk not in g.index: continue
            cred = g.loc[sk,"mid"] - g.loc[lk,"mid"]
            if cred < min_cred: continue
            d = abs(cred - target_cred)
            if d < bestd:
                bestd = d
                best = {"expiration":exp, "dte":int(g.loc[sk,"dte"]),
                        "short":int(sk), "long":int(lk),
                        "credit": float(cred)}
    return best

def exit_cost(exit_df, short, long_, exp):
    e = exit_df[exit_df["expiration"]==exp].set_index("strike")
    if short not in e.index or long_ not in e.index: return None, None
    sm = float(e.loc[short,"mid"]); lm = float(e.loc[long_,"mid"])
    sa = float(e.loc[short,"ask_px_00"]); lb = float(e.loc[long_,"bid_px_00"])
    return sm - lm, sa - lb

def run_ic(target_dte, put_cred_pct, call_cred_pct, width=100, vix_filter=True):
    """Iron Condor: independent put + call spreads with separate credit targets."""
    rows = []
    dte_lo, dte_hi = max(1,target_dte-1), target_dte+2
    put_files = {fp.stem.split("_")[-1]: fp for fp in PUT_DIR.glob("short_dte_*.parquet")}
    call_files = {fp.stem.split("_")[-1]: fp for fp in CALL_DIR.glob("short_dte_calls_*.parquet")}
    common = sorted(set(put_files) & set(call_files))
    if not common:
        return pd.DataFrame()
    for ds in common:
        ed = datetime.strptime(ds, "%Y%m%d").date()
        if ed.weekday() == 3: continue
        if vix_filter:
            vo, vp = get_vix(ed)
            if vo is None or vp is None: continue
            if vo>=25 or (vo-vp)>=1: continue
        pe, px = load_side(put_files[ds], ed)
        ce, cx = load_side(call_files[ds], ed)
        if pe is None or ce is None or pe.empty or ce.empty: continue
        put_target  = width * put_cred_pct
        call_target = width * call_cred_pct
        put_min  = width * 0.03
        call_min = width * 0.03
        ps = find_spread(pe, dte_lo, dte_hi, put_target,  put_min,  width, is_put=True)
        cs = find_spread(ce, dte_lo, dte_hi, call_target, call_min, width, is_put=False)
        if ps is None or cs is None: continue
        # match expirations? prefer same expiry
        if ps["expiration"] != cs["expiration"]: continue
        pcm, pcr = exit_cost(px, ps["short"], ps["long"], ps["expiration"])
        ccm, ccr = exit_cost(cx, cs["short"], cs["long"], cs["expiration"])
        if pcm is None or ccm is None: continue
        total_credit = ps["credit"] + cs["credit"]
        exit_mid  = pcm + ccm
        exit_real = pcr + ccr
        pnl_mid  = (total_credit - exit_mid)  * 100
        pnl_real = (total_credit - exit_real) * 100
        rows.append({"entry":ed, "dte":ps["dte"],
                     "p_short":ps["short"], "p_long":ps["long"],
                     "c_short":cs["short"], "c_long":cs["long"],
                     "p_cred":ps["credit"], "c_cred":cs["credit"], "total_cred":total_credit,
                     "exit_mid":exit_mid, "exit_real":exit_real,
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
    print("IRON CONDOR INTRADAY (PUT + CALL credit spread, 09:45 → 15:55)")
    print("="*100); print()
    for w in [50, 100, 150, 200]:
        print(f"\n### WIDTH = {w}pt ###")
        for dte in [2, 3, 5, 7]:
            for cred in [0.10, 0.20, 0.30]:
                df = run_ic(target_dte=dte, put_cred_pct=cred, call_cred_pct=cred, width=w)
                summary(df, f"DTE={dte} cred={int(cred*100)}%/{int(cred*100)}% w={w}")
            print()
