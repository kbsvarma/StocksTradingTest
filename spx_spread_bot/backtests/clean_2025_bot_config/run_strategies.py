"""Compare 5 SPXW 0DTE strategies on same data, same methodology.

Strategies (all 1 contract, 50pt wings where applicable, 1% OTM seed where applicable):
  1. BPS — Bull Put Spread (current bot strategy)
  2. CCS — Bear Call Spread (mirror of BPS on call side)
  3. IC  — Iron Condor (BPS + CCS combined)
  4. IF  — Iron Fly (sell ATM put + ATM call, 50pt wings)
  5. STRANGLE — short 1% OTM put + 1% OTM call (NO wings, defined risk via SL only)

Methodology (clean, no hindsight):
  - 10:30 ET entry
  - SPX@10:30 via put-call parity
  - Direction filter: BPS skip if down, CCS skip if up, IC/IF/STR no directional filter
  - VIX gate: skip if VIX > 25 (prior day close)
  - Min credit floors per strategy (scaled)
  - SL: trigger when net spread mid hits stop_mult × entry_credit
  - Exit: real bid/ask close-cost (worst case)
  - 1 trade per day max

Run: python -m spx_spread_bot.backtests.clean_2025_bot_config.run_strategies YEAR
"""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime, time as dtime
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf

DATA_P = Path(__file__).parent.parent / "narrow_width_databento_2026_05_21" / "spxw_p_cbbo_1m"
DATA_C = Path(__file__).parent.parent / "narrow_width_databento_2026_05_21" / "spxw_c_cbbo_1m"
DATA_C_OTM = Path(__file__).parent.parent / "narrow_width_databento_2026_05_21" / "spxw_c_otm_cbbo_1m"
OUT = Path(__file__).parent
_YEAR = sys.argv[1] if len(sys.argv) > 1 else "2025"
RESULT = OUT / f"result_strategies_{_YEAR}.json"

ENTRY_H, ENTRY_M = 10, 30
WIDTH = 50          # for spreads
OTM_PCT = 0.010     # 1%
STOP_MULTS = [2.0, 3.0]
VIX_MAX_BPS = 25.0  # BPS only trades on calm days (high VIX = crash risk for short puts)
VIX_MAX_CCS = 999.0 # CCS has NO VIX cap (high VIX = fat call premium + bearish = ideal)

# Per-strategy min credit thresholds (scaled to be similar % of width)
MIN_CRED = {
    "BPS": 1.20,
    "CCS": 1.20,
    "IC":  2.40,    # sum of both wings
    "IF":  6.00,    # narrow body, much bigger credit
    "STR": 4.00,    # naked-side, big credit
}
TARGET_CRED = {
    "BPS": 2.00, "CCS": 2.00, "IC": 4.00, "IF": 10.0, "STR": 8.0,
}

_RX = re.compile(r"[CP]0?(\d+)000$")


def _strike(sym):
    m = _RX.search(sym)
    return int(m.group(1)) if m else 0


def _load(fp):
    try:
        import fastparquet as _fp
        df = _fp.ParquetFile(str(fp)).to_pandas()
    except Exception:
        df = pd.read_parquet(fp, engine="pyarrow")
    if df.empty: return pd.DataFrame()
    df = df.dropna(subset=["bid_px_00","ask_px_00"])
    df = df[(df["bid_px_00"]>0)&(df["ask_px_00"]>0)]
    if df.empty: return pd.DataFrame()
    df["ts_event"] = pd.to_datetime(df["ts_event"], utc=True).dt.tz_convert("America/New_York")
    df["ts_min"] = df["ts_event"].dt.floor("min")
    df["strike"] = df["symbol"].apply(_strike)
    g = df.sort_values("ts_event").groupby(["ts_min","strike"]).last()
    return g[["bid_px_00","ask_px_00"]]


def _spx_parity(c, p, ts):
    try:
        cm = c.xs(ts, level="ts_min"); pm = p.xs(ts, level="ts_min")
    except KeyError: return None
    if cm.empty or pm.empty: return None
    cmid = (cm["bid_px_00"]+cm["ask_px_00"])/2
    pmid = (pm["bid_px_00"]+pm["ask_px_00"])/2
    m = pd.DataFrame({"c":cmid,"p":pmid}).dropna()
    if m.empty: return None
    m["d"] = (m["c"]-m["p"]).abs()
    b = m.sort_values("d").iloc[0]
    return float(b.name) + float(b["c"]) - float(b["p"])


def _series(p_or_c, sk, lk=None):
    """Return per-minute mid + close_cost for: single short (if lk=None) or vertical spread."""
    try:
        s = p_or_c.xs(sk, level="strike")
    except KeyError: return pd.DataFrame()
    if lk is None:
        # Single short — mid = mid_price, close_cost = ask (cost to buy back short)
        s = s.copy()
        s["mid"] = (s["bid_px_00"]+s["ask_px_00"])/2
        s["close_cost"] = s["ask_px_00"]
        return s[["mid","close_cost"]]
    try:
        l = p_or_c.xs(lk, level="strike")
    except KeyError: return pd.DataFrame()
    m = s.join(l, lsuffix="_s", rsuffix="_l", how="inner")
    if m.empty: return m
    m["mid"] = (m["bid_px_00_s"]+m["ask_px_00_s"])/2 - (m["bid_px_00_l"]+m["ask_px_00_l"])/2
    m["close_cost"] = m["ask_px_00_s"] - m["bid_px_00_l"]
    return m[["mid","close_cost"]]


def _credit_seek(quotes, target_strike, side, width, min_cred, tgt_cred):
    """Pick strike with credit closest to target.
    side='P' → BPS (short higher strike, long lower)
    side='C' → CCS (short lower strike, long higher)
    """
    strikes = sorted(quotes.index.tolist())
    if not strikes: return None
    cands = sorted(strikes, key=lambda s: abs(s-target_strike))[:5]
    best = None; best_d = float("inf")
    for sk in cands:
        if side == "P":
            lk = sk - width
        else:  # C
            lk = sk + width
        if sk not in quotes.index or lk not in quotes.index: continue
        s = quotes.loc[sk]; l = quotes.loc[lk]
        smid = (s["bid_px_00"]+s["ask_px_00"])/2
        lmid = (l["bid_px_00"]+l["ask_px_00"])/2
        cred = smid - lmid
        if cred < min_cred: continue
        d = abs(cred - tgt_cred)
        if d < best_d:
            best_d = d
            best = (int(sk), int(lk), float(cred))
    return best


def _credit_short_only(quotes, target_strike, min_cred, tgt_cred):
    """Pick a single short strike (for STRANGLE legs)."""
    strikes = sorted(quotes.index.tolist())
    if not strikes: return None
    cands = sorted(strikes, key=lambda s: abs(s-target_strike))[:5]
    best = None; best_d = float("inf")
    for sk in cands:
        if sk not in quotes.index: continue
        s = quotes.loc[sk]
        smid = (s["bid_px_00"]+s["ask_px_00"])/2
        if smid < min_cred: continue
        d = abs(smid - tgt_cred)
        if d < best_d:
            best_d = d
            best = (int(sk), float(smid))
    return best


def _sim_one_leg(spread, entry_ts, credit, mult):
    """Walk forward minute by minute. Returns dict."""
    stop = credit * mult
    walk = spread[spread.index >= entry_ts]
    if walk.empty: return None
    for ts, r in walk.iterrows():
        if float(r["mid"]) >= stop:
            return dict(sl=True, pnl_mid=credit-float(r["mid"]), pnl_real=credit-float(r["close_cost"]), exit_ts=str(ts))
    f = walk.iloc[-1]
    return dict(sl=False, pnl_mid=credit-float(f["mid"]), pnl_real=credit-float(f["close_cost"]), exit_ts=str(f.name))


def _combo_pnl(s1, s2):
    """Combine 2 simultaneous legs into single trade outcome."""
    return dict(
        sl=(s1["sl"] or s2["sl"]),
        pnl_mid=s1["pnl_mid"] + s2["pnl_mid"],
        pnl_real=s1["pnl_real"] + s2["pnl_real"],
    )


def main():
    print(f"[strategies-{_YEAR}] loading SPX/VIX...")
    spx = yf.Ticker("^GSPC").history(start=f"{int(_YEAR)-1}-12-15", end=f"{int(_YEAR)+1}-01-05")
    spx.index = spx.index.tz_localize(None) if spx.index.tz else spx.index
    vix = yf.Ticker("^VIX").history(start=f"{int(_YEAR)-1}-12-15", end=f"{int(_YEAR)+1}-01-05")
    vix.index = vix.index.tz_localize(None) if vix.index.tz else vix.index

    p_files = sorted(DATA_P.glob(f"spxw_p_cbbo_{_YEAR}*.parquet"))
    print(f"[strategies-{_YEAR}] {len(p_files)} put files")

    results = {strat: {m: [] for m in STOP_MULTS} for strat in ["BPS","CCS","IC","IF","STR"]}
    skip_count = {"vix":0, "no_call":0, "no_parity":0, "no_qual":0}
    days_attempted = 0

    for i, pfp in enumerate(p_files):
        ds = pfp.stem.split("_")[-1]
        day = pd.Timestamp(f"{ds[:4]}-{ds[4:6]}-{ds[6:]}")
        try: spx_open = float(spx.loc[day, "Open"])
        except: continue
        try: v = vix.loc[:day].iloc[-2]["Close"] if day in vix.index else vix.loc[:day].iloc[-1]["Close"]
        except: v = 20.0
        # VIX gate is per-strategy — don't skip the whole day here, check inside each strategy block

        cfp = DATA_C / pfp.name.replace("_p_","_c_")
        if not cfp.exists():
            skip_count["no_call"] += 1; continue

        pq = _load(pfp); cq = _load(cfp)
        if pq.empty or cq.empty: continue

        # Merge OTM call data (covers 1-5% OTM strikes needed for CCS short/long legs)
        cfp_otm = DATA_C_OTM / pfp.name.replace("_p_","_c_")
        if cfp_otm.exists():
            cq_otm = _load(cfp_otm)
            if not cq_otm.empty:
                cq = pd.concat([cq, cq_otm])
                cq = cq[~cq.index.duplicated(keep="last")]

        entry_ts = day.tz_localize("America/New_York").replace(hour=ENTRY_H, minute=ENTRY_M)
        spx_1030 = _spx_parity(cq, pq, entry_ts)
        if spx_1030 is None:
            skip_count["no_parity"] += 1; continue

        days_attempted += 1

        try: pq_entry = pq.xs(entry_ts, level="ts_min")
        except KeyError: continue
        try: cq_entry = cq.xs(entry_ts, level="ts_min")
        except KeyError: continue

        # ─── BPS (short put 1% OTM, long put 50pt below) ─────────────────
        if spx_1030 >= spx_open and v < VIX_MAX_BPS:  # direction + VIX gate
            target_p = int(round(spx_1030 * (1-OTM_PCT) / 5) * 5)
            pick = _credit_seek(pq_entry, target_p, "P", WIDTH, MIN_CRED["BPS"], TARGET_CRED["BPS"])
            if pick:
                sk, lk, cred = pick
                spread = _series(pq, sk, lk)
                if not spread.empty:
                    for mult in STOP_MULTS:
                        s = _sim_one_leg(spread, entry_ts, cred, mult)
                        if s: results["BPS"][mult].append({**s, "day":str(day.date()), "cred":cred, "vix":round(v,1)})

        # ─── CCS (short call 1% above, long call 50pt above) ────────────
        # NO VIX cap: high VIX → fat premiums + bearish market = ideal CCS conditions
        if spx_1030 <= spx_open and v < VIX_MAX_CCS:
            target_c = int(round(spx_1030 * (1+OTM_PCT) / 5) * 5)
            pick = _credit_seek(cq_entry, target_c, "C", WIDTH, MIN_CRED["CCS"], TARGET_CRED["CCS"])
            if pick:
                sk, lk, cred = pick
                spread = _series(cq, sk, lk)
                if not spread.empty:
                    for mult in STOP_MULTS:
                        s = _sim_one_leg(spread, entry_ts, cred, mult)
                        if s: results["CCS"][mult].append({**s, "day":str(day.date()), "cred":cred, "vix":round(v,1)})

        # ─── IC = BPS + CCS combined (no direction filter, both placed) ───
        target_p_ic = int(round(spx_1030 * (1-OTM_PCT) / 5) * 5)
        target_c_ic = int(round(spx_1030 * (1+OTM_PCT) / 5) * 5)
        bps_pick = _credit_seek(pq_entry, target_p_ic, "P", WIDTH, MIN_CRED["BPS"], TARGET_CRED["BPS"])
        ccs_pick = _credit_seek(cq_entry, target_c_ic, "C", WIDTH, MIN_CRED["BPS"], TARGET_CRED["BPS"])
        if bps_pick and ccs_pick:
            p_sk, p_lk, p_cred = bps_pick
            c_sk, c_lk, c_cred = ccs_pick
            ic_cred = p_cred + c_cred
            if ic_cred >= MIN_CRED["IC"]:
                p_spread = _series(pq, p_sk, p_lk)
                c_spread = _series(cq, c_sk, c_lk)
                if not p_spread.empty and not c_spread.empty:
                    for mult in STOP_MULTS:
                        # IC stop = each side stops on its own mark hitting stop_mult * its own credit
                        sp = _sim_one_leg(p_spread, entry_ts, p_cred, mult)
                        sc = _sim_one_leg(c_spread, entry_ts, c_cred, mult)
                        if sp and sc:
                            combo = _combo_pnl(sp, sc)
                            combo["day"] = str(day.date()); combo["cred"] = ic_cred
                            results["IC"][mult].append(combo)

        # ─── IF = ATM put + ATM call (close to ATM) + 50pt wings ──────────
        # Pick ATM strike (closest to spx_1030)
        atm = int(round(spx_1030 / 5) * 5)
        if atm in pq_entry.index and atm in cq_entry.index:
            # Build BPS at ATM (short atm put, long atm-50 put)
            if (atm-WIDTH) in pq_entry.index and (atm+WIDTH) in cq_entry.index:
                p_s = pq_entry.loc[atm]; p_l = pq_entry.loc[atm-WIDTH]
                c_s = cq_entry.loc[atm]; c_l = cq_entry.loc[atm+WIDTH]
                p_cred = (p_s["bid_px_00"]+p_s["ask_px_00"])/2 - (p_l["bid_px_00"]+p_l["ask_px_00"])/2
                c_cred = (c_s["bid_px_00"]+c_s["ask_px_00"])/2 - (c_l["bid_px_00"]+c_l["ask_px_00"])/2
                if_cred = p_cred + c_cred
                if if_cred >= MIN_CRED["IF"] and p_cred > 0 and c_cred > 0:
                    p_spread = _series(pq, atm, atm-WIDTH)
                    c_spread = _series(cq, atm, atm+WIDTH)
                    if not p_spread.empty and not c_spread.empty:
                        for mult in STOP_MULTS:
                            sp = _sim_one_leg(p_spread, entry_ts, float(p_cred), mult)
                            sc = _sim_one_leg(c_spread, entry_ts, float(c_cred), mult)
                            if sp and sc:
                                combo = _combo_pnl(sp, sc)
                                combo["day"] = str(day.date()); combo["cred"] = float(if_cred)
                                results["IF"][mult].append(combo)

        # ─── STRANGLE = short OTM put + short OTM call (NO wings) ──────────
        target_p_s = int(round(spx_1030 * (1-OTM_PCT) / 5) * 5)
        target_c_s = int(round(spx_1030 * (1+OTM_PCT) / 5) * 5)
        p_pick = _credit_short_only(pq_entry, target_p_s, MIN_CRED["BPS"], TARGET_CRED["BPS"])
        c_pick = _credit_short_only(cq_entry, target_c_s, MIN_CRED["BPS"], TARGET_CRED["BPS"])
        if p_pick and c_pick:
            p_sk, p_cred = p_pick
            c_sk, c_cred = c_pick
            str_cred = p_cred + c_cred
            if str_cred >= MIN_CRED["STR"]:
                p_spread = _series(pq, p_sk)  # single-leg "spread"
                c_spread = _series(cq, c_sk)
                if not p_spread.empty and not c_spread.empty:
                    for mult in STOP_MULTS:
                        sp = _sim_one_leg(p_spread, entry_ts, p_cred, mult)
                        sc = _sim_one_leg(c_spread, entry_ts, c_cred, mult)
                        if sp and sc:
                            combo = _combo_pnl(sp, sc)
                            combo["day"] = str(day.date()); combo["cred"] = str_cred
                            results["STR"][mult].append(combo)

        if (i+1) % 50 == 0:
            print(f"  processed {i+1}/{len(p_files)}, attempted={days_attempted}")

    print(f"\n[strategies-{_YEAR}] DONE  attempted={days_attempted}  skip={skip_count}")

    out = {"year": _YEAR, "days_attempted": days_attempted, "skip": skip_count, "by_strategy": {}}
    print()
    print(f'{"STRAT":>6} {"SL":>4} {"N":>4} {"WIN%":>6} {"SL%":>5} {"AVG_C":>6} {"AVG_PNL":>8} {"TOTAL_MID":>10} {"TOTAL_REAL":>11} {"WORST":>8} {"SHARPE":>7}')
    print("-"*110)
    for strat in ["BPS","CCS","IC","IF","STR"]:
        out["by_strategy"][strat] = {}
        for mult in STOP_MULTS:
            ts = results[strat][mult]
            if not ts:
                print(f'{strat:>6} {mult:>3.1f}× {"0":>4}  no trades')
                continue
            n = len(ts)
            wins = sum(1 for t in ts if t["pnl_real"] > 0)
            sls = sum(1 for t in ts if t["sl"])
            avg_c = np.mean([t["cred"] for t in ts])
            pnls_real = [t["pnl_real"]*100 for t in ts]
            pnls_mid = [t["pnl_mid"]*100 for t in ts]
            tot_mid = sum(pnls_mid); tot_real = sum(pnls_real)
            avg = tot_real / n
            worst = min(pnls_real)
            sharpe = np.mean(pnls_real) / (np.std(pnls_real)+1e-9) * np.sqrt(250) if n>1 else 0
            print(f'{strat:>6} {mult:>3.1f}× {n:>4} {wins/n*100:>5.1f}% {sls/n*100:>4.1f}% ${avg_c:>5.2f} ${avg:>+7.0f} ${tot_mid:>+10,.0f} ${tot_real:>+10,.0f} ${worst:>+7,.0f} {sharpe:>+7.2f}')
            out["by_strategy"][strat][str(mult)] = dict(
                trades=n, win_pct=round(wins/n*100,1), sl_pct=round(sls/n*100,1),
                avg_credit=round(avg_c,2), avg_pnl_real=round(avg,0),
                total_pnl_mid=round(tot_mid,0), total_pnl_real=round(tot_real,0),
                worst_real=round(worst,0), sharpe=round(sharpe,2))

    RESULT.write_text(json.dumps(out, indent=2, default=str))
    print(f"\nsaved {RESULT.name}")


if __name__ == "__main__":
    main()
