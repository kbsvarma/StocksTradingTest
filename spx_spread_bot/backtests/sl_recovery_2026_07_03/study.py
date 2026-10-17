"""SL RECOVERY-RATE STUDY — SPX 0DTE bull put spread (memory: sl_recovery_rate_study_todo)

Question: of trades whose intraday mark hits k x entry credit, how many recover
(expire worthless / beat the SL exit) vs go on to worse losses? Which SL
multiplier maximizes EV vs holding to settlement?

Data (all local, no network):
  - SPXW put CBBO 1-min:  narrow_width_databento_2026_05_21/spxw_p_cbbo_1m/
  - SPXW call CBBO 1-min: narrow_width_databento_2026_05_21/spxw_c_cbbo_1m/  (parity spot)
  - SPX daily OHLC:       narrow_width_databento_2026_05_21/spx_intraday_cache.parquet (yfinance daily, despite name)
  - SPX minute (2021+):   edge_hunt_cache/spx_minute/YYYYMMDD.parquet (cross-check only)
  - VIX daily:            edge_hunt_cache/vix_panel.parquet (vix_open)

Method (per task spec):
  1. Entry 10:30 ET: short put at strike closest to spot_1030 x (1 - 1%), long put 50pts
     lower. Entry bar = CBBO bar labeled 10:31 ET (contains quotes from the 10:30 minute,
     i.e. the BBO the bot sees right after 10:30); fallback bar 10:30. Credit = mid - mid.
     spot_1030 = put-call parity median over strikes with both C and P quotes at the entry
     bar (validated vs edge_hunt minute spot: 3c agreement on 2026-05-15).
  2. PRIMARY universe = bot filters: weekdays (run_days historically [0-4]) + direction
     filter spot_1030 >= day open (webull_bot/main.py _check_direction_filter: fails iff
     spx_price < spx_open). SECONDARY = all days. VIX gate NOT applied (covered by the
     VIX-regime split; bot's live VIX 12-25 gate noted in report).
  3. Mark path: per-minute spread mid, grid 10:32 -> 16:00 ET labels.
     DEVIATION from spec (documented in REPORT.md): cbbo-1m emits a bar only on a quote
     UPDATE, not every minute — deep-OTM legs on quiet days rest 10-30 min between
     updates while the standing quote stays valid. A literal "ffill <=5 min then drop
     if >15% missing" rule dropped 62% of the 2026 sample (biased toward quiet winning
     days). We therefore ffill without limit (quote persistence, seeded from the entry
     bar) and instead TRACK staleness (max quote age, frac of minutes with age > 5).
     A stale mark cannot spuriously trigger an SL (a rising mark requires an update);
     it can only slightly delay/overstate a hit, which is conservative for the SL side.
     Drop rule kept for true no-quote coverage: >15% of grid minutes with no standing
     quote at all -> drop day (counted).
  4. k in {1.5, 2.0, 2.5, 3.0, 4.0}: SL hit = first grid minute with mark >= k*credit.
     No-SL outcome = credit - max(0, Ks - close) + max(0, Kl - close), close = daily
     16:00 close (PM settlement proxy). RECOVERED := no_sl_pnl > sl_exit_pnl (headline
     slippage); SL-SAVED otherwise.
  5. SL exit pnl = credit - (hit_mark + slippage), slippage in {0.05, 0.10, 0.15}
     (real SL fills in trades.csv recorded exactly at the 2x trigger -> ~0 measured
     slippage vs trigger, but n=2 and likely records the stop level; sensitivity kept).

Usage:
  /opt/anaconda3/bin/python3 study.py --start 2026-01-02 --end 2026-05-29 --tag sample
  /opt/anaconda3/bin/python3 study.py --full --tag full
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from multiprocessing import Pool
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
BT = HERE.parent
PUT_DIR = BT / "narrow_width_databento_2026_05_21" / "spxw_p_cbbo_1m"
CALL_DIR = BT / "narrow_width_databento_2026_05_21" / "spxw_c_cbbo_1m"
DAILY_SPX = BT / "narrow_width_databento_2026_05_21" / "spx_intraday_cache.parquet"
VIX_PANEL = BT / "edge_hunt_cache" / "vix_panel.parquet"
SPX_MINUTE_DIR = BT / "edge_hunt_cache" / "spx_minute"

ET = "America/New_York"
OTM_PCT = 0.010          # live config otm_pct
WIDTH = 50               # live config spread_width
K_LIST = [1.5, 2.0, 2.5, 3.0, 4.0]
SLIPPAGES = [0.05, 0.10, 0.15]
HEADLINE_SLIP = 0.10
SNAP_TOL = 10.0          # max |chosen strike - target| (early years have sparser strikes)
STALE_FLAG_MIN = 5       # minutes — quote age above this counts as "stale" (tracked)
MAX_MISSING_FRAC = 0.15  # frac of grid with NO standing quote at all -> drop
PX_MAX = 1e5             # databento null sentinel filter


def _load_chain(path: Path) -> pd.DataFrame | None:
    try:
        df = pd.read_parquet(path, columns=["bid_px_00", "ask_px_00", "symbol"])
    except Exception:
        return None
    if df.empty:
        return None
    df.index = df.index.tz_convert(ET)
    df["strike"] = df["symbol"].str[-8:].astype(int) / 1000.0
    df = df[(df.bid_px_00 >= 0) & (df.ask_px_00 >= df.bid_px_00) & (df.ask_px_00 < PX_MAX)]
    df["mid"] = (df.bid_px_00 + df.ask_px_00) / 2.0
    return df


def _bar_mids(df: pd.DataFrame, hhmm: str) -> pd.Series:
    bar = df.between_time(hhmm, hhmm)
    if bar.empty:
        return pd.Series(dtype=float)
    return bar.groupby("strike")["mid"].median()


def _parity_spot(puts: pd.DataFrame, day8: str, hhmm: str) -> tuple[float | None, int]:
    cpath = CALL_DIR / f"spxw_c_cbbo_{day8}.parquet"
    calls = _load_chain(cpath)
    if calls is None:
        return None, 0
    pm = _bar_mids(puts, hhmm)
    cm = _bar_mids(calls, hhmm)
    both = pd.concat([cm.rename("c"), pm.rename("p")], axis=1).dropna()
    if len(both) < 3:
        return None, len(both)
    est = both.index.to_series().reset_index(drop=True) + both.c.values - both.p.values
    return float(est.median()), len(both)


def _minute_spot(day8: str, hhmm: str) -> float | None:
    p = SPX_MINUTE_DIR / f"{day8}.parquet"
    if not p.exists():
        return None
    try:
        m = pd.read_parquet(p, columns=["spx"])
        v = m.between_time(hhmm, hhmm)["spx"]
        return float(v.iloc[0]) if len(v) else None
    except Exception:
        return None


def process_day(args: tuple[str, float, float]) -> dict:
    """One trading day. args = (day8, day_open, day_close). Returns record dict."""
    day8, day_open, day_close = args
    rec: dict = {"date": day8, "status": None}

    puts = _load_chain(PUT_DIR / f"spxw_p_cbbo_{day8}.parquet")
    if puts is None:
        rec["status"] = "skip_no_put_file"
        return rec

    # --- spot at entry (parity primary; edge_hunt minute fallback + cross-check) ---
    entry_label = None
    spot = None
    for hhmm in ("10:31", "10:30"):
        spot, n_strikes = _parity_spot(puts, day8, hhmm)
        if spot is not None:
            entry_label = hhmm
            break
    minute_spot = _minute_spot(day8, "10:30")
    if spot is None:
        if minute_spot is not None:
            spot, entry_label = minute_spot, "10:31"
            rec["spot_src"] = "edge_hunt_minute"
        else:
            rec["status"] = "skip_no_spot"
            return rec
    else:
        rec["spot_src"] = "parity"
    rec["spot_1030"] = round(spot, 2)
    rec["minute_spot_1030"] = round(minute_spot, 2) if minute_spot is not None else np.nan
    rec["entry_label"] = entry_label

    # --- strike selection ---
    strikes = np.array(sorted(puts["strike"].unique()))
    target = spot * (1 - OTM_PCT)
    short_k = float(strikes[np.abs(strikes - target).argmin()])
    if abs(short_k - target) > SNAP_TOL:
        rec["status"] = "skip_snap_too_far"
        return rec
    long_k = short_k - WIDTH
    if long_k not in strikes:
        rec["status"] = "skip_no_long_strike"
        return rec
    rec["short_k"], rec["long_k"] = short_k, long_k

    # --- entry credit at the entry bar (no ffill at entry) ---
    legs = puts[puts["strike"].isin([short_k, long_k])]
    em = _bar_mids(legs, entry_label)
    if short_k not in em.index or long_k not in em.index:
        rec["status"] = "skip_no_entry_quotes"
        return rec
    credit = float(em[short_k] - em[long_k])
    if credit <= 0:
        rec["status"] = "skip_nonpos_credit"
        return rec
    rec["credit"] = round(credit, 4)

    # --- intraday mark path 10:32 -> 16:00 (grid of bar labels) ---
    day_ts = pd.Timestamp(day8).tz_localize(ET)
    grid = pd.date_range(day_ts + pd.Timedelta(hours=10, minutes=32),
                         day_ts + pd.Timedelta(hours=16), freq="1min")
    leg_mid, max_age, stale_frac = {}, 0.0, 0.0
    for k in (short_k, long_k):
        # raw minute bars from the entry bar onward (entry bar seeds the ffill)
        raw = (legs[legs["strike"] == k]["mid"].groupby(level=0).median()
               .loc[lambda s: s.index >= day_ts + pd.Timedelta(hours=10, minutes=30)])
        on_grid = raw.reindex(grid)
        # quote persistence: unlimited ffill, seeded with last value at/before grid start
        seed = raw[raw.index <= grid[0]]
        filled = on_grid.copy()
        if len(seed) and pd.isna(filled.iloc[0]):
            filled.iloc[0] = seed.iloc[-1]
        filled = filled.ffill()
        leg_mid[k] = filled
        # staleness diagnostics: minutes since last raw update
        has_bar = on_grid.notna()
        age = (~has_bar).astype(int).groupby(has_bar.cumsum()).cumsum()
        max_age = max(max_age, float(age.max()))
        stale_frac = max(stale_frac, float((age > STALE_FLAG_MIN).mean()))
    mark = leg_mid[short_k] - leg_mid[long_k]
    n_missing = int(mark.isna().sum())
    rec["n_missing_min"] = n_missing
    rec["max_quote_age_min"] = max_age
    rec["stale_frac"] = round(stale_frac, 4)
    if n_missing / len(grid) > MAX_MISSING_FRAC:
        rec["status"] = "skip_no_standing_quote"
        return rec

    # --- settlement (PM = 16:00 close) ---
    no_sl = credit - max(0.0, short_k - day_close) + max(0.0, long_k - day_close)
    no_sl = max(no_sl, credit - WIDTH)  # cap at max loss
    rec["close"] = day_close
    rec["day_open"] = day_open
    rec["no_sl_pnl"] = round(no_sl, 4)
    rec["direction_pass"] = bool(spot >= day_open)
    rec["dow"] = int(pd.Timestamp(day8).dayofweek)
    rec["mark_max"] = round(float(mark.max()), 4) if mark.notna().any() else np.nan

    # --- SL hits per k ---
    for k in K_LIST:
        thr = k * credit
        hitmask = mark >= thr
        kk = str(k).replace(".", "")
        if hitmask.any():
            t = hitmask.idxmax()
            hm = float(mark.loc[t])
            rec[f"hit_{kk}"] = True
            rec[f"hit_time_{kk}"] = t.strftime("%H:%M")
            rec[f"hit_mark_{kk}"] = round(hm, 4)
        else:
            rec[f"hit_{kk}"] = False
    rec["status"] = "ok"
    return rec


# ────────────────────────────────────────────────────────────────────────────
def build_universe(start: str, end: str) -> list[tuple[str, float, float]]:
    daily = pd.read_parquet(DAILY_SPX)
    daily.index = pd.to_datetime(daily.index)
    files = sorted(PUT_DIR.glob("spxw_p_cbbo_*.parquet"))
    out, no_daily = [], 0
    for f in files:
        day8 = f.stem[-8:]
        d = pd.Timestamp(day8)
        if not (pd.Timestamp(start) <= d <= pd.Timestamp(end)):
            continue
        if d not in daily.index:
            no_daily += 1
            continue
        row = daily.loc[d]
        out.append((day8, float(row["Open"]), float(row["Close"])))
    print(f"[universe] {len(out)} days with put file + daily OHLC "
          f"({no_daily} put-file days dropped: no daily OHLC)", flush=True)
    return out


def aggregate(df: pd.DataFrame, tag: str) -> None:
    ok = df[df.status == "ok"].copy()
    skips = df[df.status != "ok"].groupby("status").size()
    print("\n[skips]"); print(skips.to_string() if len(skips) else "  none")

    # VIX regimes
    vix = pd.read_parquet(VIX_PANEL)[["vix_open"]]
    vix.index = pd.to_datetime(vix.index)
    ok["dt"] = pd.to_datetime(ok["date"])
    ok = ok.merge(vix, left_on="dt", right_index=True, how="left")
    ok["vix_regime"] = pd.cut(ok["vix_open"], [0, 15, 20, 30, np.inf],
                              labels=["<15", "15-20", "20-30", ">30"])
    ok["year"] = ok["dt"].dt.year

    # hits.csv (long format)
    hit_rows = []
    for k in K_LIST:
        kk = str(k).replace(".", "")
        h = ok[ok[f"hit_{kk}"] == True]  # noqa: E712
        for _, r in h.iterrows():
            hm = r[f"hit_mark_{kk}"]
            row = {"date": r["date"], "k": k, "credit": r["credit"],
                   "short_k": r["short_k"], "long_k": r["long_k"],
                   "hit_time": r[f"hit_time_{kk}"], "hit_mark": hm,
                   "spx_close": round(r["close"], 2), "no_sl_pnl": r["no_sl_pnl"],
                   "expired_worthless": bool(abs(r["no_sl_pnl"] - r["credit"]) < 1e-9),
                   "vix_open": r["vix_open"], "direction_pass": r["direction_pass"]}
            for s in SLIPPAGES:
                row[f"sl_exit_pnl_s{int(s*100):02d}"] = round(r["credit"] - (hm + s), 4)
            sl_exit = r["credit"] - (hm + HEADLINE_SLIP)
            row["class"] = "RECOVERED" if r["no_sl_pnl"] > sl_exit else "SL_SAVED"
            hit_rows.append(row)
    hits = pd.DataFrame(hit_rows).sort_values(["k", "date"]) if hit_rows else pd.DataFrame()
    hits.to_csv(HERE / f"hits_{tag}.csv", index=False)

    # summary.csv
    summ = []
    for uni, sub in (("PRIMARY_direction_filter", ok[ok.direction_pass]),
                     ("SECONDARY_all_days", ok)):
        n = len(sub)
        for k in K_LIST:
            kk = str(k).replace(".", "")
            hitcol = sub[f"hit_{kk}"] == True  # noqa: E712
            nh = int(hitcol.sum())
            for s in SLIPPAGES:
                hm = sub.loc[hitcol, f"hit_mark_{kk}"]
                sl_exit = sub.loc[hitcol, "credit"] - (hm + s)
                nosl_hit = sub.loc[hitcol, "no_sl_pnl"]
                recovered = nosl_hit > sl_exit
                strat = sub["no_sl_pnl"].copy()
                strat.loc[hitcol] = sl_exit
                delta = strat - sub["no_sl_pnl"]
                summ.append({
                    "universe": uni, "k": k, "slippage": s,
                    "n_days": n, "n_hits": nh,
                    "hit_rate": round(nh / n, 4) if n else np.nan,
                    "recovery_rate": round(recovered.mean(), 4) if nh else np.nan,
                    "n_recovered": int(recovered.sum()),
                    "avg_given_up_recovered": round((nosl_hit - sl_exit)[recovered].mean(), 4) if recovered.any() else np.nan,
                    "avg_saved_true_losers": round((sl_exit - nosl_hit)[~recovered].mean(), 4) if (~recovered).any() else np.nan,
                    "ev_delta_per_trade": round(delta.mean(), 4) if n else np.nan,
                    "total_pnl_delta": round(delta.sum(), 2),
                    "sl_strategy_total": round(strat.sum(), 2),
                    "no_sl_total": round(sub["no_sl_pnl"].sum(), 2),
                    "worst_day_sl": round(strat.min(), 2) if n else np.nan,
                    "worst_day_no_sl": round(sub["no_sl_pnl"].min(), 2) if n else np.nan,
                })
    summary = pd.DataFrame(summ)
    summary.to_csv(HERE / f"summary_{tag}.csv", index=False)

    # yearly + VIX-regime tables (headline slippage, PRIMARY universe)
    prim = ok[ok.direction_pass]
    ytab, vtab = [], []
    for group_col, store in (("year", ytab), ("vix_regime", vtab)):
        for gval, g in prim.groupby(group_col, observed=True):
            row = {group_col: gval, "n": len(g), "no_sl_total": round(g["no_sl_pnl"].sum(), 2)}
            for k in K_LIST:
                kk = str(k).replace(".", "")
                hitcol = g[f"hit_{kk}"] == True  # noqa: E712
                strat = g["no_sl_pnl"].copy()
                strat.loc[hitcol] = g.loc[hitcol, "credit"] - (g.loc[hitcol, f"hit_mark_{kk}"] + HEADLINE_SLIP)
                row[f"sl{k}_total"] = round(strat.sum(), 2)
                row[f"sl{k}_hits"] = int(hitcol.sum())
            store.append(row)
    pd.DataFrame(ytab).to_csv(HERE / f"yearly_{tag}.csv", index=False)
    pd.DataFrame(vtab).to_csv(HERE / f"vix_regime_{tag}.csv", index=False)

    ok.drop(columns=["dt"]).to_csv(HERE / f"daily_{tag}.csv", index=False)

    # console digest
    print(f"\n[{tag}] ok_days={len(ok)}  primary(direction-pass)={len(prim)}")
    view = summary[(summary.slippage == HEADLINE_SLIP)]
    print(view[["universe", "k", "n_days", "n_hits", "hit_rate", "recovery_rate",
                "ev_delta_per_trade", "total_pnl_delta", "worst_day_sl",
                "worst_day_no_sl"]].to_string(index=False))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2013-04-01")
    ap.add_argument("--end", default="2026-05-29")
    ap.add_argument("--full", action="store_true")
    ap.add_argument("--tag", default="run")
    ap.add_argument("--procs", type=int, default=10)
    args = ap.parse_args()
    if args.full:
        args.start, args.end = "2013-04-01", "2026-05-29"

    days = build_universe(args.start, args.end)
    t0 = time.monotonic()
    recs = []
    with Pool(args.procs) as pool:
        for i, rec in enumerate(pool.imap_unordered(process_day, days, chunksize=8)):
            recs.append(rec)
            if (i + 1) % 200 == 0:
                dt = time.monotonic() - t0
                print(f"  [{i+1}/{len(days)}] {dt:.0f}s  ({(i+1)/dt:.1f} days/s)", flush=True)
    dt = time.monotonic() - t0
    print(f"[done] {len(recs)} days in {dt:.0f}s ({len(recs)/max(dt,1e-9):.1f} days/s)")
    df = pd.DataFrame(recs)
    df.to_parquet(HERE / f"raw_{args.tag}.parquet")
    aggregate(df, args.tag)


if __name__ == "__main__":
    main()
