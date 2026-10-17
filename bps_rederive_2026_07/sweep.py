"""IS grid sweep runner. Per day: build minute grids once; per (entry,width,otm):
compute strikes via live selection rule, entry credit (conservative NBBO cross),
SL cost path, settlement; then evaluate all SL multipliers + gate combos on top.

Writes one row per (day, cell) to parquet shards per year. Restart-safe per year.
Grid (locked in PREREGISTRATION_BPS.md):
  entry: 09:35,10:00,10:30,11:00,11:30,12:00,12:30,13:00,13:30,14:00
  width: 25,50,75,100   otm: 0.5%,1.0%,1.5%
  dir:   none | spx_ge_open | spx_ge_open_25bp   (ES-combo variant computed in analysis
         stage from stored day flags + ES sessions data)
  sl:    0(none),1.5,2.0,3.0    vix gate: on|off (flags stored per day, applied in analysis)
Credit floor/target scale with width: floor=1.20*w/50, target=2.00*w/50 (DEVIATIONS.md).
"""
import sys
from pathlib import Path
import numpy as np
import pandas as pd

from chain import load_day, parity_spot
from engine import build_minute_grid, round5

ET = "America/New_York"
OUT = Path("data/sweep")
OUT.mkdir(parents=True, exist_ok=True)

ENTRIES = [(9,35),(10,0),(10,30),(11,0),(11,30),(12,0),(12,30),(13,0),(13,30),(14,0)]
WIDTHS = [25.0, 50.0, 75.0, 100.0]
OTMS = [0.005, 0.010, 0.015]
SLS = [0.0, 1.5, 2.0, 3.0]
COMMISSION_LEG_RT = 1.30   # $ per leg per transaction (entry 2 legs; exit adds 2)

def cache_day_tags():
    base = Path("../spx_spread_bot/backtests/narrow_width_databento_2026_05_21/spxw_p_cbbo_1m")
    new = Path("data/new_days")
    tags = {f.stem.split("_")[-1] for f in base.glob("*.parquet")}
    tags |= {f.stem.split("_")[-1].replace("spxw_p_cbbo_","") for f in new.glob("spxw_p_cbbo_*.parquet")}
    return sorted(tags)

def select_spread(pbid, pask, t, spot, width, otm):
    """Live selection rule, width-scaled credit target/floor. Returns tuple or None."""
    target_k = round5(spot * (1 - otm))
    floor = 1.20 * width / 50.0
    target_credit = 2.00 * width / 50.0
    cols = pbid.columns
    cand = sorted(cols, key=lambda s: abs(s - target_k))[:5]
    best, best_diff = None, np.inf
    for s_k in cand:
        below = [k for k in cols if k <= s_k - width]
        if not below:
            continue
        l_k = max(below)
        if s_k - l_k < width * 0.8:
            continue
        try:
            sb = pbid.at[t, s_k]; sa = pask.at[t, s_k]
            lb = pbid.at[t, l_k]; la = pask.at[t, l_k]
        except KeyError:
            continue
        if any(np.isnan(x) for x in (sb, sa, lb, la)) or sb <= 0 or la <= 0:
            continue
        mid = (sb + sa) / 2 - (lb + la) / 2
        if mid < floor:
            continue
        diff = abs(mid - target_credit)
        if diff < best_diff:
            best_diff, best = diff, (s_k, l_k, sb, sa, lb, la, mid)
    return best

def fast_spot_factory(pbid, pask, cbid, cask):
    """Vectorized parity spot from aligned minute grids (shared strike columns)."""
    common = [k for k in pbid.columns if k in cbid.columns]
    pb = pbid[common].to_numpy(); pa = pask[common].to_numpy()
    cb = cbid[common].to_numpy(); ca = cask[common].to_numpy()
    ks = np.array(common, dtype=float)
    def spot_i(i):
        pm = (pb[i] + pa[i]) / 2.0
        cm = (cb[i] + ca[i]) / 2.0
        ok = (pb[i] > 0) & (ca[i] > 0) & ~np.isnan(pm) & ~np.isnan(cm) & (pa[i] >= pb[i]) & (ca[i] >= cb[i])
        if ok.sum() == 0:
            return np.nan
        dist = np.abs(cm - pm)
        dist[~ok] = np.inf
        idx = np.argsort(dist)[:5]
        idx = idx[np.isfinite(dist[idx])]
        if len(idx) == 0:
            return np.nan
        return float(np.median(ks[idx] + cm[idx] - pm[idx]))
    return spot_i

def run_day(tag, vix, spx_close_map):
    d0 = pd.Timestamp(tag).tz_localize(ET)
    dkey = pd.Timestamp(tag)
    day = load_day(tag)
    if day is None:
        return []
    spx_close = spx_close_map.get(dkey, np.nan)
    if np.isnan(spx_close):
        return []
    vrow = vix.loc[dkey] if dkey in vix.index else None
    vix_open = float(vrow["open"]) if vrow is not None else np.nan
    pbid, pask = build_minute_grid(day["P"])
    cbid, cask = build_minute_grid(day["C"])
    # cap at 16:00 ET: expiring 0DTE series stop trading at 16:00; later bars are
    # stale/post-close and must not drive SL fires
    cutoff = d0 + pd.Timedelta(hours=16)
    pbid = pbid[pbid.index <= cutoff]; pask = pask[pask.index <= cutoff]
    cbid = cbid.reindex(pbid.index); cask = cask.reindex(pbid.index)
    minutes = pbid.index
    if len(minutes) < 60:
        return []
    spot_i = fast_spot_factory(pbid, pask, cbid, cask)
    min_ix0 = {m: i for i, m in enumerate(minutes)}
    def spot_at_minute(m):
        i = min_ix0.get(m)
        return spot_i(i) if i is not None else np.nan
    # spot anchors
    s_open = spot_at_minute(d0 + pd.Timedelta(hours=9, minutes=32))
    if np.isnan(s_open):
        s_open = spot_at_minute(d0 + pd.Timedelta(hours=9, minutes=35))
    rows = []
    bid_np = pbid.to_numpy(); ask_np = pask.to_numpy()
    col_ix = {k: i for i, k in enumerate(pbid.columns)}
    min_ix = {m: i for i, m in enumerate(minutes)}

    # --- SCAN mode: incumbent fidelity. From 10:30 to 14:30, enter at the FIRST
    # minute where (a) live selection (w50, otm1.0, floor 1.20) succeeds and
    # (b) direction filter passes at that minute (live couples them at entry).
    t_lo = d0 + pd.Timedelta(hours=10, minutes=30)
    t_hi = d0 + pd.Timedelta(hours=14, minutes=30)
    scan_minutes = [m for m in minutes if t_lo <= m <= t_hi]
    for m in scan_minutes:
        spot_m = spot_at_minute(m)
        if np.isnan(spot_m) or np.isnan(s_open) or spot_m < s_open:
            continue
        sel = select_spread(pbid, pask, m, spot_m, 50.0, 0.010)
        if sel is None:
            continue
        ks, kl, sb, sa, lb, la, mid = sel
        credit = sb - la
        if credit < 0.05:
            continue
        ti = min_ix[m]
        si, li = col_ix[ks], col_ix[kl]
        cost_path = ask_np[ti+1:, si] - bid_np[ti+1:, li]
        valid = ~np.isnan(cost_path)
        settle = credit - (max(0.0, ks - spx_close) - max(0.0, kl - spx_close))
        for sl in SLS:
            if sl > 0:
                mask = valid & (cost_path >= sl * credit)
                if mask.any():
                    j = int(np.argmax(mask))
                    pnl_pts = credit - float(cost_path[j]); status = "stop"
                else:
                    pnl_pts, status = settle, "expire"
            else:
                pnl_pts, status = settle, "expire"
            fee = COMMISSION_LEG_RT * (4 if status == "stop" else 2)
            rows.append({"date": dkey, "entry": "SCAN1030", "width": 50.0, "otm": 0.010,
                         "sl": sl, "status": status, "credit": round(credit, 2),
                         "mid": round(mid, 2), "short": ks, "long": kl,
                         "pnl_usd": pnl_pts * 100 - fee, "vix_open": vix_open,
                         "dir_ok": True, "dir25_ok": spot_m >= s_open * 1.0025,
                         "blind_frac": 1 - valid.mean() if len(valid) else 1.0})
        break  # entered once — live trades once per day

    for (hh, mm) in ENTRIES:
        t = d0 + pd.Timedelta(hours=hh, minutes=mm)
        if t not in min_ix:
            continue
        ti = min_ix[t]
        spot = spot_at_minute(t)
        if np.isnan(spot):
            continue
        dir_ok = spot >= s_open if not np.isnan(s_open) else np.nan
        dir25_ok = spot >= s_open * 1.0025 if not np.isnan(s_open) else np.nan
        for width in WIDTHS:
            for otm in OTMS:
                sel = select_spread(pbid, pask, t, spot, width, otm)
                if sel is None:
                    continue
                ks, kl, sb, sa, lb, la, mid = sel
                credit = sb - la
                if credit < 0.05:
                    continue
                si, li = col_ix[ks], col_ix[kl]
                cost_path = ask_np[ti+1:, si] - bid_np[ti+1:, li]
                valid = ~np.isnan(cost_path)
                settle = credit - (max(0.0, ks - spx_close) - max(0.0, kl - spx_close))
                for sl in SLS:
                    if sl > 0:
                        mask = valid & (cost_path >= sl * credit)
                        if mask.any():
                            j = int(np.argmax(mask))
                            pnl_pts = credit - float(cost_path[j])
                            status = "stop"
                        else:
                            pnl_pts, status = settle, "expire"
                    else:
                        pnl_pts, status = settle, "expire"
                    fee = COMMISSION_LEG_RT * (4 if status == "stop" else 2)
                    rows.append({
                        "date": dkey, "entry": f"{hh:02d}:{mm:02d}", "width": width,
                        "otm": otm, "sl": sl, "status": status,
                        "credit": round(credit, 2), "mid": round(mid, 2),
                        "short": ks, "long": kl,
                        "pnl_usd": pnl_pts * 100 - fee,
                        "vix_open": vix_open, "dir_ok": dir_ok, "dir25_ok": dir25_ok,
                        "blind_frac": 1 - valid.mean() if len(valid) else 1.0,
                    })
    return rows

def main(years):
    vix = pd.read_parquet("data/vix_daily_cboe.parquet")
    spx = pd.read_csv("data/spx_daily_cboe.csv", parse_dates=["DATE"]).set_index("DATE")["SPX"]
    spx_close_map = spx.to_dict()
    tags = cache_day_tags()
    for year in years:
        out = OUT / f"sweep_{year}.parquet"
        if out.exists():
            print(f"[skip] {year} done")
            continue
        ytags = [t for t in tags if t.startswith(str(year))]
        allrows = []
        for i, tag in enumerate(ytags):
            try:
                allrows.extend(run_day(tag, vix, spx_close_map))
            except Exception as e:
                print(f"[err] {tag}: {e}")
            if (i + 1) % 25 == 0:
                print(f"  {year}: {i+1}/{len(ytags)} days")
        df = pd.DataFrame(allrows)
        df.to_parquet(out)
        print(f"[ok] {year}: {len(ytags)} days -> {len(df)} rows")

if __name__ == "__main__":
    ys = [int(y) for y in sys.argv[1:]] or list(range(2013, 2021))
    main(ys)
