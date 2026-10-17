"""Build NDX trade outcomes — vectorized, NO build-level hindsight gate.
Apply VIX gate at EVAL time only using vix_prev/vix_open (known at 9:30)."""
import sys, re, time
from pathlib import Path
import numpy as np, pandas as pd

CACHE = Path(__file__).parent
DATA_ROOT = CACHE.parent / "narrow_width_databento_2026_05_21"
DATA_P = DATA_ROOT / "ndxp_p_cbbo_1m"
NDX_MIN = CACHE / "ndx_minute"

YS = int(sys.argv[1]) if len(sys.argv)>1 else 2025
YE = int(sys.argv[2]) if len(sys.argv)>2 else 2025
OUT = CACHE / f"ndx_trades_{YS}_{YE}.parquet"

# NDX uses $25 strike grid. Widths in multiples of 25.
WIDTHS = [25, 50, 75, 100, 125, 150, 175, 200, 225, 250]
CRED_PCTS = [0.04, 0.10, 0.20, 0.30]
ENTRY_TIMES = [(9,45),(10,0),(10,15),(10,30),(10,45),(11,0)]
CONFIGS = [(w, w*cp*0.5, w*cp, cp, f"W{w}_C{int(cp*100):02d}")
           for w in WIDTHS for cp in CRED_PCTS]

RX = re.compile(r"[CP]0?(\d+)000$")
def _k(s): m=RX.search(s); return int(m.group(1)) if m else 0

def _load_pivoted(fp):
    df = pd.read_parquet(fp)
    df = df.dropna(subset=["bid_px_00","ask_px_00"])
    df = df[(df["bid_px_00"]>0)&(df["ask_px_00"]>0)]
    if df.empty: return None, None, None
    df["ts_event"] = pd.to_datetime(df["ts_event"],utc=True).dt.tz_convert("America/New_York")
    df["ts_min"] = df["ts_event"].dt.floor("min")
    df["strike"] = df["symbol"].apply(_k)
    df["mid"] = (df["bid_px_00"]+df["ask_px_00"])/2
    df = df.sort_values("ts_event").groupby(["ts_min","strike"]).last()
    return df["mid"].unstack(level="strike"), df["bid_px_00"].unstack(level="strike"), df["ask_px_00"].unstack(level="strike")

print("[ndx-trades] loading context caches...")
vix_panel = pd.read_parquet(CACHE / "vix_panel.parquet")
ndx_daily = pd.read_parquet(DATA_ROOT / "spot_cache_NDX.parquet")
ndx_daily["ret"] = ndx_daily["Close"].pct_change()
ndx_daily["rv10"] = ndx_daily["ret"].rolling(10).std()*np.sqrt(252)*100
ndx_daily["ret5"] = ndx_daily["Close"].pct_change(5)*100
delta = ndx_daily["Close"].diff()
g = delta.clip(lower=0).rolling(14).mean(); l = -delta.clip(upper=0).rolling(14).mean()
ndx_daily["rsi14"] = 100-100/(1+g/l)

p_files = sorted([f for f in DATA_P.glob("ndxp_p_cbbo_*.parquet") if YS <= int(f.stem.split("_")[-1][:4]) <= YE])
print(f"[ndx-trades] {len(p_files)} days\n")

t0 = time.time()
rows = []
for i, pfp in enumerate(p_files):
    ds = pfp.stem.split("_")[-1]
    day = pd.Timestamp(f"{ds[:4]}-{ds[4:6]}-{ds[6:]}")
    try:
        ndx_row = ndx_daily.loc[day]
        ndx_open = float(ndx_row["Open"]); ndx_close = float(ndx_row["Close"])
    except: continue
    prev_idx = ndx_daily.index.get_indexer([day], method="ffill")[0]
    if prev_idx < 1: continue
    prev_close = float(ndx_daily.iloc[prev_idx-1]["Close"])
    prev_rsi = float(ndx_daily.iloc[prev_idx-1]["rsi14"]) if not pd.isna(ndx_daily.iloc[prev_idx-1]["rsi14"]) else np.nan

    try:
        v = vix_panel.loc[day]
        vix_today = float(v["vix_close"]); vix_open_today = float(v["vix_open"])
    except (KeyError, ValueError): continue
    try: vix_prev = float(vix_panel.loc[:day].iloc[-2]["vix_close"])
    except: vix_prev = 20
    vix_overnight_gap = vix_open_today - vix_prev
    # NO build-level VIX gate — eval-time only

    ndx_min_fp = NDX_MIN / f"{ds}.parquet"
    if not ndx_min_fp.exists(): continue
    ndx_min = pd.read_parquet(ndx_min_fp)
    if ndx_min.empty: continue
    ndx_ser = ndx_min["ndx"]
    n_930 = float(ndx_ser.iloc[0])
    em = float(ndx_min.iloc[0]["atm_cmid"] + ndx_min.iloc[0]["atm_pmid"])
    overnight_gap = (ndx_open/prev_close - 1)*100

    mid_mat, bid_mat, ask_mat = _load_pivoted(pfp)
    if mid_mat is None: continue

    entry_data = {}
    for h, m in ENTRY_TIMES:
        ets = day.tz_localize("America/New_York").replace(hour=h, minute=m)
        if ets not in ndx_ser.index:
            idx = ndx_ser.index.get_indexer([ets], method="nearest")[0]
            if idx<0 or abs((ndx_ser.index[idx]-ets).total_seconds())>300: continue
            n_e = float(ndx_ser.iloc[idx])
        else:
            n_e = float(ndx_ser.loc[ets])
        entry_data[(h,m)] = (ets, n_e)

    for width, min_cred, tgt_cred, cred_pct, wlabel in CONFIGS:
        for h, m in ENTRY_TIMES:
            if (h,m) not in entry_data: continue
            ets, n_entry = entry_data[(h,m)]
            if ets in mid_mat.index:
                row_idx = ets
            else:
                pos = mid_mat.index.get_indexer([ets], method="nearest")[0]
                if pos<0: continue
                row_idx = mid_mat.index[pos]
            # NDX uses $25 strike grid, scan strikes 96-100% OTM band
            min_strike = int(round(n_entry*0.96/25)*25)
            max_strike = int(round(n_entry/25)*25) - 25
            row_mid = mid_mat.loc[row_idx]
            cands = [s for s in mid_mat.columns if min_strike<=s<=max_strike and not np.isnan(row_mid[s])]
            best = None; bd = float("inf")
            for sk in cands:
                lk = sk - width
                if lk not in mid_mat.columns or np.isnan(row_mid[lk]): continue
                cred = row_mid[sk] - row_mid[lk]
                if cred < min_cred: continue
                d = abs(cred - tgt_cred)
                if d < bd: bd=d; best=(int(sk), int(lk), float(cred))
            if best is None: continue
            sk, lk, cred = best
            after = mid_mat.index >= ets
            mid_s = mid_mat.loc[after, sk]; mid_l = mid_mat.loc[after, lk]
            bid_s = bid_mat.loc[after, sk]; ask_s = ask_mat.loc[after, sk]
            bid_l = bid_mat.loc[after, lk]; ask_l = ask_mat.loc[after, lk]
            df_sp = pd.DataFrame({"mid": mid_s - mid_l, "cc": ask_s - bid_l}).dropna()
            if df_sp.empty: continue
            cc_final = float(df_sp["cc"].iloc[-1])
            pnl_hold = (cred - cc_final)*100  # NDX multiplier is $100 same as SPX
            cmax = df_sp["mid"].cummax()
            sl_out = {}
            for mult in [1.0,1.5,2.0,2.5,3.0]:
                hit = cmax >= cred*mult
                cc_exit = float(df_sp["cc"].loc[hit.idxmax()]) if hit.any() else cc_final
                sl_out[f"pnl_sl_{mult}x"] = (cred - cc_exit)*100
            cmin = df_sp["mid"].cummin()
            pt_out = {}
            for pt in [0.25, 0.50, 0.75]:
                hit = cmin <= cred*(1-pt)
                cc_exit = float(df_sp["cc"].loc[hit.idxmax()]) if hit.any() else cc_final
                pt_out[f"pnl_pt_{int(pt*100)}"] = (cred - cc_exit)*100
            otm_pct_actual = (n_entry - sk)/n_entry*100
            rows.append({
                "day": str(day.date()), "year": day.year, "dow": day.dayofweek,
                "width": width, "cred_pct_tgt": cred_pct, "width_label": wlabel,
                "entry_hm": f"{h:02d}:{m:02d}",
                "ndx_open": ndx_open, "ndx_close": ndx_close, "ndx_entry": n_entry, "ndx_930": n_930,
                "open_to_entry_pct": (n_entry/n_930-1)*100,
                "overnight_gap": overnight_gap,
                "short": sk, "long": lk, "cred": cred,
                "cred_pct_width": cred/width*100, "otm_pct_actual": otm_pct_actual,
                "pnl_hold": pnl_hold, **sl_out, **pt_out,
                "em": em, "vix": vix_today, "vix_open": vix_open_today, "vix_prev": vix_prev,
                "vix_overnight_gap": vix_overnight_gap,
                "rsi14_prev": prev_rsi,
                "rv10": float(ndx_row["rv10"]) if not pd.isna(ndx_row["rv10"]) else np.nan,
                "ret5d": float(ndx_row["ret5"]) if not pd.isna(ndx_row["ret5"]) else np.nan,
            })
    if (i+1) % 25 == 0:
        elapsed = time.time() - t0
        rate = (i+1)/elapsed
        eta = (len(p_files)-i-1)/rate
        print(f"  {i+1}/{len(p_files)}  rows={len(rows)} ({rate:.1f}/s, ETA {eta:.0f}s)")

df = pd.DataFrame(rows)
df.to_parquet(OUT)
print(f"\n[ndx-trades] saved {len(df)} rows ({df['day'].nunique()} days) → {OUT}")
print(f"  time: {time.time()-t0:.1f}s")
print(df.groupby(["width","cred_pct_tgt"]).size().unstack(fill_value=0).to_string())
