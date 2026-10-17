"""Build trade outcomes — VECTORIZED with pivoted parquets.
Per day: pivot put parquet ONCE to (ts_min × strike) matrices, then per trade just look up columns.
Eliminates ~280k xs() calls inside the loop.
"""
import sys, re, time
from pathlib import Path
import numpy as np, pandas as pd

CACHE = Path(__file__).parent
DATA_ROOT = CACHE.parent / "narrow_width_databento_2026_05_21"
DATA_P = DATA_ROOT / "spxw_p_cbbo_1m"
SPX_MIN_CACHE = CACHE / "spx_minute"

YEAR_START = int(sys.argv[1]) if len(sys.argv) > 1 else 2024
YEAR_END = int(sys.argv[2]) if len(sys.argv) > 2 else 2026
OUT = CACHE / f"trades_{YEAR_START}_{YEAR_END}.parquet"

WIDTHS = [5, 10, 15, 20, 25, 30, 35, 40, 45, 50]
CRED_PCTS = [0.04, 0.10, 0.20, 0.30]
ENTRY_TIMES = [(9,45),(10,0),(10,15),(10,30),(10,45),(11,0)]
CONFIGS = [(w, w*cp*0.5, w*cp, cp, f"W{w:02d}_C{int(cp*100):02d}") for w in WIDTHS for cp in CRED_PCTS]

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
    mid_mat = df["mid"].unstack(level="strike")
    bid_mat = df["bid_px_00"].unstack(level="strike")
    ask_mat = df["ask_px_00"].unstack(level="strike")
    return mid_mat, bid_mat, ask_mat

print("[trades-v2] loading context caches...")
vix_panel = pd.read_parquet(CACHE / "vix_panel.parquet")
spx_daily = pd.read_parquet(DATA_ROOT / "spot_cache_GSPC.parquet")
spx_daily["ret"]=spx_daily["Close"].pct_change()
spx_daily["rv5"]=spx_daily["ret"].rolling(5).std()*np.sqrt(252)*100
spx_daily["rv10"]=spx_daily["ret"].rolling(10).std()*np.sqrt(252)*100
spx_daily["rv21"]=spx_daily["ret"].rolling(21).std()*np.sqrt(252)*100
spx_daily["ma20"]=spx_daily["Close"].rolling(20).mean()
spx_daily["ret5"]=spx_daily["Close"].pct_change(5)*100
spx_daily["ret20"]=spx_daily["Close"].pct_change(20)*100
delta=spx_daily["Close"].diff()
g=delta.clip(lower=0).rolling(14).mean(); l=-delta.clip(upper=0).rolling(14).mean()
spx_daily["rsi14"]=100-100/(1+g/l)
print(f"  vix_panel: {vix_panel.shape}, spx_daily: {spx_daily.shape}")

fomc_dates = pd.to_datetime([
    "2024-01-31","2024-03-20","2024-05-01","2024-06-12","2024-07-31","2024-09-18","2024-11-07","2024-12-18",
    "2025-01-29","2025-03-19","2025-05-07","2025-06-18","2025-07-30","2025-09-17","2025-10-29","2025-12-10",
    "2026-01-28","2026-03-18","2026-05-06",
])

p_files = sorted([f for f in DATA_P.glob("spxw_p_cbbo_*.parquet")
                  if YEAR_START <= int(f.stem.split("_")[-1][:4]) <= YEAR_END])
print(f"\n[trades-v2] {len(p_files)} days to process\n")

t0 = time.time()
rows = []
for i, pfp in enumerate(p_files):
    ds = pfp.stem.split("_")[-1]
    day = pd.Timestamp(f"{ds[:4]}-{ds[4:6]}-{ds[6:]}")

    try:
        spx_row = spx_daily.loc[day]
        spx_open=float(spx_row["Open"]); spx_close=float(spx_row["Close"])
    except: continue
    prev_idx = spx_daily.index.get_indexer([day], method="ffill")[0]
    if prev_idx < 1: continue
    prev_close = float(spx_daily.iloc[prev_idx-1]["Close"])
    prev_rsi = float(spx_daily.iloc[prev_idx-1]["rsi14"]) if not pd.isna(spx_daily.iloc[prev_idx-1]["rsi14"]) else np.nan

    try:
        v = vix_panel.loc[day]
        vix_today=float(v["vix_close"]); vix_open_today=float(v["vix_open"])
        vix_high=float(v["vix_high"]); vix_low=float(v["vix_low"])
        vix9d=float(v["vix9d_close"]); vix3m=float(v["vix3m_close"]); vvix=float(v["vvix_close"])
    except (KeyError, ValueError): continue
    try: vix_prev = float(vix_panel.loc[:day].iloc[-2]["vix_close"])
    except: vix_prev = 20
    vix_change = vix_today - vix_prev
    vix_overnight_gap = vix_open_today - vix_prev
    vix_intraday_change = vix_today - vix_open_today
    vix_range = vix_high - vix_low
    # NO BUILD-LEVEL GATE — VIX gate (if any) must be applied at eval time
    # using only known-at-entry features (vix_prev, vix_open). The previous
    # `if vix_today >= 25.0: continue` was HINDSIGHT and inflated results.

    spx_min_file = SPX_MIN_CACHE / f"{ds}.parquet"
    if not spx_min_file.exists(): continue
    spx_min = pd.read_parquet(spx_min_file)
    if spx_min.empty: continue
    spx_ser = spx_min["spx"]
    s_930 = float(spx_ser.iloc[0])
    em = float(spx_min.iloc[0]["atm_cmid"] + spx_min.iloc[0]["atm_pmid"])

    day_start = day.tz_localize("America/New_York").replace(hour=9, minute=30)
    orb_window = spx_ser[(spx_ser.index >= day_start) & (spx_ser.index <= day_start + pd.Timedelta(minutes=60))]
    orb_high = float(orb_window.max()) if not orb_window.empty else np.nan
    orb_low  = float(orb_window.min()) if not orb_window.empty else np.nan
    orb_range_pct = (orb_high - orb_low)/s_930*100 if not (np.isnan(orb_high) or np.isnan(orb_low)) and s_930>0 else np.nan
    overnight_gap = (spx_open/prev_close - 1)*100

    mid_mat, bid_mat, ask_mat = _load_pivoted(pfp)
    if mid_mat is None: continue

    entry_data = {}
    for h, m in ENTRY_TIMES:
        ets = day.tz_localize("America/New_York").replace(hour=h, minute=m)
        if ets not in spx_ser.index:
            idx = spx_ser.index.get_indexer([ets], method="nearest")[0]
            if idx < 0 or abs((spx_ser.index[idx]-ets).total_seconds()) > 300: continue
            s_e = float(spx_ser.iloc[idx])
        else:
            s_e = float(spx_ser.loc[ets])
        entry_data[(h,m)] = (ets, s_e)

    def orb_sig_at(ets):
        if np.isnan(orb_range_pct) or orb_range_pct < 0.2: return None
        if ets <= day_start + pd.Timedelta(minutes=60): return None
        w = spx_ser[(spx_ser.index >= day_start + pd.Timedelta(minutes=60)) & (spx_ser.index <= ets)]
        if w.empty: return "no_break"
        if (w > orb_high).any(): return "up_break"
        if (w < orb_low).any(): return "down_break"
        return "no_break"

    for width, min_cred, tgt_cred, cred_pct, wlabel in CONFIGS:
        for h, m in ENTRY_TIMES:
            if (h,m) not in entry_data: continue
            ets, s_entry = entry_data[(h,m)]

            if ets in mid_mat.index:
                row_idx = ets
            else:
                pos = mid_mat.index.get_indexer([ets], method="nearest")[0]
                if pos < 0: continue
                row_idx = mid_mat.index[pos]

            min_strike = int(round(s_entry*0.96/5)*5)
            max_strike = int(round(s_entry/5)*5) - 5
            row_mid = mid_mat.loc[row_idx]
            cands = [s for s in mid_mat.columns
                      if min_strike <= s <= max_strike and not np.isnan(row_mid[s])]
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

            # Spread series from pivoted matrices (column lookup)
            after = mid_mat.index >= ets
            mid_s = mid_mat.loc[after, sk]; mid_l = mid_mat.loc[after, lk]
            bid_s = bid_mat.loc[after, sk]; ask_s = ask_mat.loc[after, sk]
            bid_l = bid_mat.loc[after, lk]; ask_l = ask_mat.loc[after, lk]
            df_sp = pd.DataFrame({"mid": mid_s - mid_l, "cc": ask_s - bid_l}).dropna()
            if df_sp.empty: continue

            cc_final = float(df_sp["cc"].iloc[-1])
            pnl_hold = (cred - cc_final)*100

            cmax = df_sp["mid"].cummax()
            sl_out = {}
            for mult in [1.0,1.5,2.0,2.5,3.0]:
                hit = cmax >= cred*mult
                cc_exit = float(df_sp["cc"].loc[hit.idxmax()]) if hit.any() else cc_final
                sl_out[f"pnl_sl_{mult}x"] = (cred - cc_exit)*100

            spx_aligned = spx_ser.reindex(df_sp.index, method="ffill")
            below = spx_aligned <= sk
            cc_exit = float(df_sp["cc"].loc[below.idxmax()]) if below.any() else cc_final
            pnl_spatial = (cred - cc_exit)*100

            cmin = df_sp["mid"].cummin()
            pt_out = {}
            for pt in [0.25, 0.50, 0.75]:
                hit = cmin <= cred*(1-pt)
                cc_exit = float(df_sp["cc"].loc[hit.idxmax()]) if hit.any() else cc_final
                pt_out[f"pnl_pt_{int(pt*100)}"] = (cred - cc_exit)*100

            otm_pct_actual = (s_entry - sk)/s_entry*100

            rows.append({
                "day": str(day.date()), "year": day.year, "dow": day.dayofweek,
                "width": width, "cred_pct_tgt": cred_pct, "width_label": wlabel,
                "entry_hm": f"{h:02d}:{m:02d}",
                "spx_open": spx_open, "spx_close": spx_close,
                "spx_entry": s_entry, "spx_930": s_930,
                "open_to_entry_pct": (s_entry/spx_open-1)*100,
                "spx_close_pct_from_entry": (spx_close/s_entry-1)*100,
                "spx_low_pct_from_entry": (float(spx_ser[spx_ser.index>=ets].min())/s_entry-1)*100 if not spx_ser[spx_ser.index>=ets].empty else np.nan,
                "overnight_gap": overnight_gap,
                "short": sk, "long": lk, "cred": cred,
                "cred_pct_width": cred/width*100, "otm_pct_actual": otm_pct_actual,
                "pnl_hold": pnl_hold, "pnl_spatial_sl": pnl_spatial,
                **sl_out, **pt_out,
                "orb_high": orb_high, "orb_low": orb_low, "orb_range_pct": orb_range_pct,
                "orb_signal": orb_sig_at(ets),
                "em": em, "em_pct": em/s_930*100 if s_930>0 else np.nan,
                "vix": vix_today, "vix_open": vix_open_today, "vix_prev": vix_prev,
                "vix_change": vix_change, "vix_overnight_gap": vix_overnight_gap,
                "vix_intraday_change": vix_intraday_change, "vix_range": vix_range,
                "vix9d": vix9d, "vix3m": vix3m, "vvix": vvix,
                "rsi14_prev": prev_rsi,
                "rv10": float(spx_row["rv10"]) if not pd.isna(spx_row["rv10"]) else np.nan,
                "ret5d": float(spx_row["ret5"]) if not pd.isna(spx_row["ret5"]) else np.nan,
                "above_ma20": (spx_open - float(spx_row["ma20"]))/float(spx_row["ma20"])*100 if not pd.isna(spx_row["ma20"]) else np.nan,
                "days_until_fomc": min((fomc_dates[fomc_dates > day][0] - day).days if len(fomc_dates[fomc_dates > day]) else 999, 30),
                "days_since_fomc": min((day - fomc_dates[fomc_dates <= day][-1]).days if len(fomc_dates[fomc_dates <= day]) else 999, 30),
            })

    if (i+1) % 25 == 0:
        elapsed = time.time() - t0
        rate = (i+1) / elapsed
        eta = (len(p_files) - i - 1) / rate
        print(f"  {i+1}/{len(p_files)}  rows={len(rows)}  ({rate:.2f} days/s, ETA {eta:.0f}s)")

df = pd.DataFrame(rows)
df.to_parquet(OUT)
print(f"\n[trades-v2] saved {len(df)} rows ({df['day'].nunique()} days) → {OUT}")
print(f"  total time: {time.time()-t0:.1f}s")
print(df.groupby(["width","cred_pct_tgt"]).size().unstack(fill_value=0).to_string())
