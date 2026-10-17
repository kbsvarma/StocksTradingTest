"""Verify the claimed ATM-bot backtest numbers from memory.

Config (per memory note):
  - 45pt width, 30% credit target ($13.50), 15% min credit ($6.75)
  - Entry 09:45 ET (13:45 UTC)
  - Skip Thursday
  - VIX gate: vix_open < 25 AND vix_overnight_gap < 1
  - SL: 3.0x entry credit (paid debit ≥ 3*credit closes position)
  - No PT, hold to SL or expiry (cash-settle to SPX close)
  - Years 2021-2026 YTD

Use SPX 0DTE put cbbo 1-min data + SPX OHLC for cash settlement + VIX panel.
Two P&L tracks: midpoint and realistic ask/bid fills.
"""
import re
from datetime import datetime, date
from pathlib import Path
import pandas as pd
import numpy as np

CACHE = Path(__file__).parent
DATA  = CACHE.parent / "narrow_width_databento_2026_05_21" / "spxw_p_cbbo_1m"
SPX   = pd.read_parquet(CACHE.parent / "narrow_width_databento_2026_05_21" / "spot_cache_GSPC.parquet")
VIX   = pd.read_parquet(CACHE / "vix_panel.parquet")

WIDTH = 45
CREDIT_TARGET = 0.30 * WIDTH  # $13.50
MIN_CREDIT    = 0.15 * WIDTH  # $6.75
SL_MULT       = 3.0
ENTRY_HHUTC, ENTRY_MMUTC = 13, 45
EOD_HHUTC, EOD_MMUTC     = 20, 0

RX = re.compile(r"P0?(\d+)000$")
def parse_strike(s):
    m = RX.search(s.strip()); return int(m.group(1)) if m else None

def get_vix(d):
    ts = pd.Timestamp(d)
    if ts not in VIX.index: return None, None
    vo = float(VIX.loc[ts, "vix_open"])
    idx = VIX.index.get_indexer([ts], method="ffill")[0]
    if idx < 1: return vo, None
    return vo, float(VIX.iloc[idx-1]["vix_close"])

def settle_bps(spx_close, short_k, long_k, width):
    if spx_close >= short_k: return 0.0
    if spx_close >= long_k:  return short_k - spx_close
    return width

def run_year(year):
    """Run backtest for one year. Returns DataFrame of trades."""
    files = sorted(DATA.glob(f"spxw_p_cbbo_{year}*.parquet"))
    rows = []
    sl_hits = 0; expired = 0; skipped_vix = 0; skipped_no_credit = 0
    for fp in files:
        ds = fp.stem.split("_")[-1]
        try: d = datetime.strptime(ds, "%Y%m%d").date()
        except: continue
        if d.weekday() == 3:  # Thursday
            continue
        ts = pd.Timestamp(d)
        if ts not in SPX.index: continue
        vo, vp = get_vix(ts)
        if vo is None or vp is None:
            skipped_vix += 1; continue
        if vo >= 25 or (vo - vp) >= 1:
            skipped_vix += 1; continue

        # Load full day cbbo
        df = pd.read_parquet(fp)
        df = df[(df["bid_px_00"]>0)&(df["ask_px_00"]>0)].copy()
        if df.empty: continue
        df["strike"] = df["symbol"].apply(parse_strike)
        df["mid"]    = (df["bid_px_00"]+df["ask_px_00"])/2
        df = df.dropna(subset=["strike","mid"])

        # Entry minute snapshot
        entry_ts_str = f"{ENTRY_HHUTC:02d}:{ENTRY_MMUTC:02d}"
        entry_df = df[df.index.strftime("%H:%M") == entry_ts_str]
        if entry_df.empty: continue
        entry_pivot = entry_df.drop_duplicates("strike").set_index("strike")[["mid","bid_px_00","ask_px_00"]]

        # Find best (short, long) pair with credit closest to $13.50, ≥ $6.75
        strikes = sorted(entry_pivot.index)
        best = None; best_d = float("inf")
        for sk in strikes:
            lk = sk - WIDTH
            if lk not in entry_pivot.index: continue
            cred = float(entry_pivot.loc[sk, "mid"] - entry_pivot.loc[lk, "mid"])
            if cred < MIN_CREDIT: continue
            d_ = abs(cred - CREDIT_TARGET)
            if d_ < best_d:
                best_d = d_
                best = (sk, lk, cred)
        if best is None:
            skipped_no_credit += 1; continue
        short_k, long_k, credit = best

        # Realistic entry credit using bid/ask: sell short at bid, buy long at ask
        short_bid = float(entry_pivot.loc[short_k, "bid_px_00"])
        long_ask  = float(entry_pivot.loc[long_k, "ask_px_00"])
        credit_real = short_bid - long_ask  # realistic = receive bid - pay ask

        # SL threshold (mid-based to detect)
        sl_threshold_mid = SL_MULT * credit

        # Walk through minute bars from entry+1 to EOD looking for SL hit
        post_entry = df[df.index >= entry_df.index[0] + pd.Timedelta(minutes=1)].copy()
        # Pivot wider strike-pair at each minute
        pivot = post_entry.pivot_table(index=post_entry.index, columns="strike", values=["mid","bid_px_00","ask_px_00"], aggfunc="last")

        sl_fired = False
        exit_minute = None
        exit_mid = None
        exit_real = None
        if not pivot.empty:
            try:
                short_mid_series = pivot[("mid", short_k)]
                long_mid_series  = pivot[("mid", long_k)]
                spread_cost_mid  = short_mid_series - long_mid_series
                # Mask to valid (both legs quoted)
                valid = spread_cost_mid.notna()
                spread_cost_mid = spread_cost_mid[valid]
                # SL: when current spread debit-to-close ≥ SL_threshold
                breach = spread_cost_mid[spread_cost_mid >= sl_threshold_mid]
                if not breach.empty:
                    sl_fired = True
                    exit_minute = breach.index[0]
                    exit_mid = float(spread_cost_mid.loc[exit_minute])
                    # Realistic close: pay short_ask, receive long_bid at that minute
                    try:
                        sa = float(pivot[("ask_px_00", short_k)].loc[exit_minute])
                        lb = float(pivot[("bid_px_00", long_k)].loc[exit_minute])
                        exit_real = sa - lb
                    except KeyError:
                        exit_real = exit_mid
            except KeyError:
                pass

        # If no SL, settle at SPX close
        if not sl_fired:
            spx_close = float(SPX.loc[ts, "Close"])
            exit_mid = settle_bps(spx_close, short_k, long_k, WIDTH)
            exit_real = exit_mid  # cash settle has no slippage
            expired += 1
        else:
            sl_hits += 1

        pnl_mid  = (credit      - exit_mid)  * 100
        pnl_real = (credit_real - exit_real) * 100

        rows.append({
            "date": d, "short": short_k, "long": long_k,
            "credit_mid": credit, "credit_real": credit_real,
            "sl_fired": sl_fired, "exit_mid": exit_mid, "exit_real": exit_real,
            "pnl_mid": pnl_mid, "pnl_real": pnl_real,
        })

    return pd.DataFrame(rows), {"sl_hits":sl_hits, "expired":expired,
                                 "skipped_vix":skipped_vix, "skipped_no_credit":skipped_no_credit}

def report(df, label):
    if df.empty:
        print(f"  {label}: NO trades"); return
    for col, tag in [("pnl_mid","mid"), ("pnl_real","real")]:
        p = df[col]; w = p[p>0]; l = p[p<0]
        print(f"  {label} [{tag}]: n={len(df)} total=${p.sum():+,.0f} avg=${p.mean():+.0f} "
              f"win={(p>0).mean()*100:.1f}% avg_win=${w.mean() if len(w) else 0:+.0f} "
              f"avg_loss=${l.mean() if len(l) else 0:+.0f} worst=${p.min():+,.0f}")

if __name__ == "__main__":
    print("="*110)
    print("VERIFY: ATM-bot config (45pt × 30% cred × 09:45 × noThu × VIX gates × SL 3.0×)")
    print("Holding to SL or SPX cash-settle; mid and ask/bid P&L both shown")
    print("="*110)
    all_rows = []
    all_stats = {}
    for year in [2021, 2022, 2023, 2024, 2025, 2026]:
        df, stats = run_year(year)
        all_stats[year] = stats
        if df.empty:
            print(f"\n>>> {year}: NO trades"); continue
        print(f"\n>>> {year} (skipped_vix={stats['skipped_vix']}, skipped_no_credit={stats['skipped_no_credit']}, sl_hits={stats['sl_hits']}, expired={stats['expired']}):")
        report(df, f"{year}")
        df["year"] = year
        all_rows.append(df)
    if all_rows:
        all_df = pd.concat(all_rows, ignore_index=True)
        print(f"\n=== COMBINED 2021-2026 ===")
        report(all_df, "ALL")
        yrs = all_df["year"].nunique()
        print(f"\n  Avg per year [mid]:  ${all_df['pnl_mid'].sum()/yrs:+,.0f}/yr")
        print(f"  Avg per year [real]: ${all_df['pnl_real'].sum()/yrs:+,.0f}/yr")
