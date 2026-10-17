"""Multi-DTE BPS backtest.

For each entry day, build a put credit spread at target DTE/OTM. Hold to expiry.
Exit price from the EXPIRY day's 0DTE data (we already have it).

Strategies tested:
  - DTE: 7, 14, 30, 45
  - Short delta proxy: target credit % of width
  - Width: 50pt (similar to legacy)
  - Hold to expiry (no PT, no SL)
"""
import re
from datetime import date, datetime, timedelta
from pathlib import Path
import pandas as pd
import numpy as np

CACHE = Path(__file__).parent
MULTI_DTE = CACHE / "multi_dte_data"
DATA_0DTE_P = CACHE.parent / "narrow_width_databento_2026_05_21" / "spxw_p_cbbo_1m"
VIX = pd.read_parquet(CACHE / "vix_panel.parquet")
SPX = pd.read_parquet(CACHE.parent / "narrow_width_databento_2026_05_21" / "spot_cache_GSPC.parquet")

RX = re.compile(r"P0?(\d+)000$")
def parse_strike(sym):
    m = RX.search(sym.strip())
    return int(m.group(1)) if m else None

def parse_expiry(sym):
    m = re.search(r"(\d{6})P", sym.strip())
    if not m: return None
    s = m.group(1)
    return datetime.strptime(s, "%y%m%d").date()

def load_entry_data(entry_date):
    """Return DataFrame of {expiration, strike, bid, ask, mid} at 9:45 entry."""
    fp = MULTI_DTE / f"spxw_multi_dte_{entry_date.strftime('%Y%m%d')}.parquet"
    if not fp.exists(): return None
    df = pd.read_parquet(fp)
    df = df[(df["bid_px_00"]>0)&(df["ask_px_00"]>0)].copy()
    if df.empty: return None
    df["strike"] = df["symbol"].apply(parse_strike)
    df["expiration"] = df["symbol"].apply(parse_expiry)
    df["mid"] = (df["bid_px_00"]+df["ask_px_00"])/2
    df = df.dropna(subset=["strike","expiration","mid"])
    df["dte"] = (df["expiration"] - entry_date).apply(lambda x: x.days if hasattr(x,'days') else 0)
    return df[["expiration","strike","dte","bid_px_00","ask_px_00","mid"]]

def find_spread(entry_data, target_dte_range, target_cred, min_cred, width):
    """Find best PUT spread at target_dte ± 2 days with credit closest to target."""
    dte_lo, dte_hi = target_dte_range
    candidates = entry_data[(entry_data["dte"]>=dte_lo) & (entry_data["dte"]<=dte_hi)].copy()
    if candidates.empty: return None
    # Group by expiration; for each expiry find best short/long pair
    best = None; best_d = float("inf")
    for exp, grp in candidates.groupby("expiration"):
        grp = grp.set_index("strike").sort_index()
        for sk in grp.index:
            lk = sk - width
            if lk not in grp.index: continue
            cred = grp.loc[sk, "mid"] - grp.loc[lk, "mid"]
            if cred < min_cred: continue
            d = abs(cred - target_cred)
            if d < best_d:
                best_d = d
                actual_dte = (exp - entry_data.iloc[0].name if hasattr(entry_data.iloc[0], 'name') else exp).days if False else (exp - candidates.iloc[0]["expiration"] + pd.Timedelta(days=grp["dte"].iloc[0])).days
                best = {
                    "expiration": exp, "dte": int(grp.loc[sk, "dte"]),
                    "short": int(sk), "long": int(lk),
                    "entry_short_bid": float(grp.loc[sk, "bid_px_00"]),
                    "entry_short_ask": float(grp.loc[sk, "ask_px_00"]),
                    "entry_long_bid":  float(grp.loc[lk, "bid_px_00"]),
                    "entry_long_ask":  float(grp.loc[lk, "ask_px_00"]),
                    "entry_credit_mid": float(cred),
                }
    return best

def get_exit_price(expiration_date, short, long_):
    """SPX options are cash-settled to SPX close. Compute settlement value directly.

    Spread settlement (put debit at expiry, = what you pay to close):
      - If SPX_close > short_strike: spread = 0 (both OTM, expire worthless)
      - If long_strike < SPX_close ≤ short_strike: spread = (short - SPX_close)
      - If SPX_close ≤ long_strike: spread = (short - long) = full width (max loss)

    Returns the cash value the long-side trader would need to PAY at expiry
    (= close_cost for the short-side trader = what bot pays to close).
    """
    d = pd.Timestamp(expiration_date)
    if d not in SPX.index: return None
    spx_close = float(SPX.loc[d, "Close"])
    if spx_close > short:
        return 0.0  # both expire worthless
    elif spx_close > long_:
        return short - spx_close  # short put ITM, long put OTM
    else:
        return short - long_  # both ITM, max loss = width

def get_vix_at_entry(entry_date):
    """Get vix_open and vix_prev_close for filtering."""
    d = pd.Timestamp(entry_date)
    if d not in VIX.index: return None, None
    vix_open = float(VIX.loc[d, "vix_open"])
    # Prev day's close
    idx = VIX.index.get_indexer([d], method="ffill")[0]
    if idx < 1: return vix_open, None
    vix_prev = float(VIX.iloc[idx-1]["vix_close"])
    return vix_open, vix_prev

def run_backtest(target_dte, target_cred_pct, width, min_cred_pct, use_vix_filter=True, verbose=False):
    """Run one config across all entry days with data."""
    files = sorted(MULTI_DTE.glob("spxw_multi_dte_*.parquet"))
    target_cred = width * target_cred_pct
    min_cred = width * min_cred_pct
    dte_range = (target_dte - 2, target_dte + 4)

    rows = []
    for fp in files:
        ds = fp.stem.split("_")[-1]
        entry = datetime.strptime(ds, "%Y%m%d").date()
        # Skip Thursday (dow=3) to match SPX winner
        if entry.weekday() == 3: continue

        # VIX filter
        if use_vix_filter:
            vix_open, vix_prev = get_vix_at_entry(entry)
            if vix_open is None or vix_prev is None: continue
            if vix_open >= 25 or (vix_open - vix_prev) >= 1: continue

        ed = load_entry_data(entry)
        if ed is None: continue
        sp = find_spread(ed, dte_range, target_cred, min_cred, width)
        if sp is None: continue

        exit_cc = get_exit_price(sp["expiration"], sp["short"], sp["long"])
        if exit_cc is None: continue

        # P&L: entry credit (mid-based optimistic) - exit close cost (ask-bid worst case)
        cred = sp["entry_credit_mid"]
        pnl = (cred - exit_cc) * 100  # SPX multiplier
        rows.append({
            "entry": str(entry), "expiration": str(sp["expiration"]),
            "dte_actual": sp["dte"], "short": sp["short"], "long": sp["long"],
            "credit": cred, "exit_cc": exit_cc, "pnl": pnl,
        })

    df = pd.DataFrame(rows)
    return df

def evaluate(df, label):
    if df.empty:
        print(f"  {label}: NO trades")
        return
    pnl = df["pnl"]
    wins = pnl[pnl>0]; losses = pnl[pnl<0]
    print(f"  {label}: n={len(df)} total=${pnl.sum():+,.0f} avg=${pnl.mean():+.0f} "
          f"win={(pnl>0).mean()*100:.1f}% avg_win=${wins.mean() if len(wins) else 0:+.0f} "
          f"avg_loss=${losses.mean() if len(losses) else 0:+.0f} worst=${pnl.min():+,.0f}")

if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2 or sys.argv[1] == "test":
        # Quick test
        print("="*100); print("MULTI-DTE BACKTEST GRID (hold to expiry, vix filter on, noThu)"); print("="*100)
        print(f"\n{'config':>50s}")
        for dte in [7, 14, 30, 45]:
            for cred_pct in [0.10, 0.20, 0.30]:
                df = run_backtest(target_dte=dte, target_cred_pct=cred_pct,
                                  width=50, min_cred_pct=0.05, use_vix_filter=True)
                evaluate(df, f"DTE={dte} cred={int(cred_pct*100)}% width=50")
