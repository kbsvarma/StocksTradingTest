"""Bias-fixed (v4 methodology) re-run of the multi-ticker 0DTE iron condor campaign.

Replicates run_multi_ic_backtest.py's strategy config:
  0.5% OTM shorts, ~6%-of-spot wings, 09:45 ET entry, 15:45 ET forced close,
  2.0x SL / 50% PT intraday walk, M/W/F 0DTE only
...but with the verify_avgo_v4.py / verify_iwm_v4.py FIXES:
  - Spot derived from put-call parity at 09:45 ET from the DataBento quotes
    themselves (NO yfinance day bars, NO `spot_o + 0.3*(spot_c-spot_o)`
    look-ahead — that used the day's CLOSE, unknowable at 09:45).
  - Timezone-correct ET->UTC conversion per date (DST-safe).
  - Five pricing scenarios per trade:
      nat_000  natural fills (sell@bid, buy@ask), no extra slip
      nat_005  natural + $0.05 combo slip each way
      nat_010  natural + $0.10 combo slip each way
      mid_005  mid fills + $0.05 combo slip each way   (realistic per 05-27 memo)
      mid_010  mid fills + $0.10 combo slip each way   (conservative)
    (Natural fills already pay the full half-spread on every leg, i.e. they are
     MORE conservative than "mid + $0.05/leg" for these names.)

CRASH-RESILIENCE:
  - One ticker at a time; per-day rows APPENDED to trades_{T}.partial.csv
    (flush per day). On restart, already-written dates are skipped.
  - Completed tickers are finalized to trades_{T}.csv via atomic os.replace.
  - A ticker with trades_{T}.csv present is skipped entirely.
  - Nothing is ever deleted or truncated.

Local caches only — no network calls of any kind.
"""
import os
import re
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

BT = Path("/Users/varmakammili/Documents/GitHub/StocksTradingTest/spx_spread_bot/backtests")
OUT = BT / "multi_ic_2026" / "closeout_2026_07_03"
OUT.mkdir(exist_ok=True)
ET = ZoneInfo("America/New_York")
UTC = timezone.utc
SYM_RE = re.compile(r"^([A-Z]+)\s+(\d{6})([PC])(\d{8})$")

# ticker -> (data_dir, glob, strike_spacing)
TICKERS = {
    "AAPL":  (BT / "multi_ic_2026/data/aapl",      "aapl_cbbo_*.parquet",  2.5),
    "AMZN":  (BT / "multi_ic_2026/data/amzn",      "amzn_cbbo_*.parquet",  2.5),
    "GOOGL": (BT / "multi_ic_2026/data/googl",     "googl_cbbo_*.parquet", 2.5),
    "MSFT":  (BT / "multi_ic_2026/data/msft",      "msft_cbbo_*.parquet",  5.0),
    "META":  (BT / "multi_ic_2026/data/meta",      "meta_cbbo_*.parquet",  5.0),
    "NVDA":  (BT / "multi_ic_2026/data/nvda_wide", "nvda_cbbo_*.parquet",  1.0),
    "TSLA":  (BT / "multi_ic_2026/data/tsla",      "tsla_cbbo_*.parquet",  5.0),
    "JNJ":   (BT / "multi_ic_2026/data/jnj_wide",  "jnj_cbbo_*.parquet",   2.5),
    "IWM":   (BT / "multi_ic_2026/data/iwm_wide",  "iwm_cbbo_*.parquet",   1.0),
}

SCENARIOS = [
    ("nat_000", False, 0.00),
    ("nat_005", False, 0.05),
    ("nat_010", False, 0.10),
    ("mid_005", True,  0.05),
    ("mid_010", True,  0.10),
]
OTM_PCT = 0.005
WING_PCT = 0.06
ENTRY_H, ENTRY_M = 9, 45
EXIT_H, EXIT_M = 15, 45

TRADE_COLS = ["ticker", "trade_date", "dow", "scenario", "spot_entry",
              "sp", "sc", "lp", "lc", "wing_w", "credit", "exit_reason", "pnl"]
SKIP_COLS = ["ticker", "trade_date", "status"]


def log(m):
    print(m, flush=True)


def find_strike(avail, target, spacing):
    snapped = round(round(target / spacing) * spacing, 2)
    if snapped in avail:
        return snapped
    for d_mult in range(1, 15):
        for sign in (+1, -1):
            t = round(snapped + sign * d_mult * spacing, 2)
            if t in avail:
                return t
    return None


def quote_at_or_near(df, ts, window_minutes=10):
    if df is None or df.empty:
        return None, None
    window = pd.Timedelta(minutes=window_minutes)
    before = df.loc[df.index <= ts]
    if not before.empty:
        last = before.iloc[-1]
        if last.name >= ts - window:
            return float(last["bid_px_00"]), float(last["ask_px_00"])
    after = df.loc[df.index > ts]
    if not after.empty:
        first = after.iloc[0]
        if first.name <= ts + window:
            return float(first["bid_px_00"]), float(first["ask_px_00"])
    return None, None


def at_or_after(df, ts):
    if df is None or df.empty:
        return None
    sub = df.loc[df.index >= ts]
    return None if sub.empty else sub.iloc[0]


def derive_spot_from_parity(legs, strikes, ts):
    """underlying ~= K + (call_mid - put_mid); pick strike with min |C-P|."""
    candidates = []
    for K in strikes:
        c_df = legs.get((K, "C"))
        p_df = legs.get((K, "P"))
        if c_df is None or p_df is None:
            continue
        c_b, c_a = quote_at_or_near(c_df, ts, window_minutes=5)
        p_b, p_a = quote_at_or_near(p_df, ts, window_minutes=5)
        if c_b is None or c_a is None or p_b is None or p_a is None:
            continue
        if pd.isna(c_b) or pd.isna(c_a) or pd.isna(p_b) or pd.isna(p_a):
            continue
        if c_a <= 0 or p_a <= 0:
            continue
        c_mid = (c_b + c_a) / 2
        p_mid = (p_b + p_a) / 2
        candidates.append((abs(c_mid - p_mid), K + (c_mid - p_mid)))
    if not candidates:
        return None
    candidates.sort()
    return candidates[0][1]


def process_day(ticker, fp, td, spacing):
    """Return (trade_rows, status). status is the day-level skip reason or 'ok'."""
    if td.weekday() not in (0, 2, 4):
        return [], "not_mwf"
    df = pd.read_parquet(fp, columns=["bid_px_00", "ask_px_00", "symbol"])
    if df.empty:
        return [], "empty_file"
    ext = df["symbol"].str.strip().str.extract(SYM_RE)
    ext.columns = ["root", "yymmdd", "right", "strike_str"]
    ok = ext["yymmdd"].notna()
    df = df[ok].copy()
    ext = ext[ok]
    df["expiry"] = pd.to_datetime(ext["yymmdd"], format="%y%m%d").dt.date
    df["right"] = ext["right"]
    df["strike"] = (ext["strike_str"].astype(float) / 1000).round(2)
    df = df[df["expiry"] == td]  # 0DTE only
    if df.empty:
        return [], "no_0dte"

    legs = {}
    for (K, R), grp in df.groupby(["strike", "right"]):
        legs[(K, R)] = grp[["bid_px_00", "ask_px_00"]].sort_index()
    strikes = sorted({k for k, _ in legs.keys()})

    entry_ts = pd.Timestamp(datetime(td.year, td.month, td.day, ENTRY_H, ENTRY_M,
                                     tzinfo=ET).astimezone(UTC))
    exit_ts = pd.Timestamp(datetime(td.year, td.month, td.day, EXIT_H, EXIT_M,
                                    tzinfo=ET).astimezone(UTC))

    spot = derive_spot_from_parity(legs, strikes, entry_ts)
    if spot is None:
        return [], "no_spot_parity"

    avail = set(strikes)
    sp = find_strike(avail, spot * (1 - OTM_PCT), spacing)
    sc = find_strike(avail, spot * (1 + OTM_PCT), spacing)
    if sp is None or sc is None:
        return [], "no_shorts"
    wing = spot * WING_PCT
    lp = find_strike(avail, sp - wing, spacing)
    lc = find_strike(avail, sc + wing, spacing)
    if lp is None or lc is None or lp >= sp or lc <= sc:
        return [], "no_wings"

    sp_df = legs.get((sp, "P")); sc_df = legs.get((sc, "C"))
    lp_df = legs.get((lp, "P")); lc_df = legs.get((lc, "C"))
    if any(x is None for x in (sp_df, sc_df, lp_df, lc_df)):
        return [], "no_leg_data"

    e_sp = at_or_after(sp_df, entry_ts); e_sc = at_or_after(sc_df, entry_ts)
    e_lp = at_or_after(lp_df, entry_ts); e_lc = at_or_after(lc_df, entry_ts)
    if any(x is None for x in (e_sp, e_sc, e_lp, e_lc)):
        return [], "no_entry_quotes"

    sp_b, sp_a = float(e_sp["bid_px_00"]), float(e_sp["ask_px_00"])
    sc_b, sc_a = float(e_sc["bid_px_00"]), float(e_sc["ask_px_00"])
    lp_b = float(e_lp["bid_px_00"]) if pd.notna(e_lp["bid_px_00"]) else 0.0
    lp_a = float(e_lp["ask_px_00"]) if pd.notna(e_lp["ask_px_00"]) else 0.01
    lc_b = float(e_lc["bid_px_00"]) if pd.notna(e_lc["bid_px_00"]) else 0.0
    lc_a = float(e_lc["ask_px_00"]) if pd.notna(e_lc["ask_px_00"]) else 0.01
    if any(pd.isna(v) for v in (sp_b, sp_a, sc_b, sc_a)):
        return [], "nan_short_entry"

    credit_nat = sp_b + sc_b - lp_a - lc_a
    credit_mid = ((sp_b + sp_a) + (sc_b + sc_a)) / 2 / 1 - ((lp_b + lp_a) + (lc_b + lc_a)) / 2
    credit_mid = (sp_b + sp_a) / 2 + (sc_b + sc_a) / 2 - (lp_b + lp_a) / 2 - (lc_b + lc_a) / 2

    # SL/PT walk frame: debit-to-close on NATURAL side (buy back shorts @ask,
    # sell wings @bid) — same convention as verify_avgo_v4.py.
    win = slice(entry_ts, exit_ts)
    sp_w = sp_df.loc[(sp_df.index >= entry_ts) & (sp_df.index <= exit_ts)]
    sc_w = sc_df.loc[(sc_df.index >= entry_ts) & (sc_df.index <= exit_ts)]
    lp_w = lp_df.loc[(lp_df.index >= entry_ts) & (lp_df.index <= exit_ts)]
    lc_w = lc_df.loc[(lc_df.index >= entry_ts) & (lc_df.index <= exit_ts)]
    al = None
    if not sp_w.empty and not sc_w.empty:
        sp_r = sp_w.rename(columns={"bid_px_00": "b_sp", "ask_px_00": "a_sp"})
        sc_r = sc_w.rename(columns={"bid_px_00": "b_sc", "ask_px_00": "a_sc"})
        lp_r = lp_w.rename(columns={"bid_px_00": "b_lp", "ask_px_00": "a_lp"})
        lc_r = lc_w.rename(columns={"bid_px_00": "b_lc", "ask_px_00": "a_lc"})
        al = (sp_r.join(sc_r, how="inner")
                  .join(lp_r[["b_lp"]], how="left")
                  .join(lc_r[["b_lc"]], how="left"))
        al["b_lp"] = al["b_lp"].fillna(0)
        al["b_lc"] = al["b_lc"].fillna(0)
        al = al.dropna(subset=["a_sp", "a_sc"])
        if al.empty:
            al = None
        else:
            al["debit"] = al["a_sp"] + al["a_sc"] - al["b_lp"] - al["b_lc"]

    # forced-close quotes at exit
    x_sp_b, x_sp_a = quote_at_or_near(sp_df, exit_ts)
    x_sc_b, x_sc_a = quote_at_or_near(sc_df, exit_ts)
    x_lp_b, x_lp_a = quote_at_or_near(lp_df, exit_ts)
    x_lc_b, x_lc_a = quote_at_or_near(lc_df, exit_ts)

    rows = []
    for name, use_mid, slip in SCENARIOS:
        credit = (credit_mid if use_mid else credit_nat) - slip
        if credit < 0.05:
            rows.append(dict(zip(TRADE_COLS, [ticker, td, td.strftime("%a"), name,
                                              round(spot, 2), sp, sc, lp, lc,
                                              round(sp - lp, 2), round(credit, 3),
                                              "thin_credit", None])))
            continue
        stop_debit = credit * 2.0
        pt_debit = credit * 0.5
        exit_val = None
        exit_reason = None
        if al is not None:
            sl_hits = al[al["debit"] >= stop_debit]
            pt_hits = al[al["debit"] <= pt_debit]
            sl_ts = sl_hits.index.min() if not sl_hits.empty else None
            pt_ts = pt_hits.index.min() if not pt_hits.empty else None
            if sl_ts is not None and (pt_ts is None or sl_ts < pt_ts):
                exit_val = float(al.loc[sl_ts, "debit"]) + slip
                exit_reason = "stop_loss"
            elif pt_ts is not None:
                exit_val = float(al.loc[pt_ts, "debit"]) + slip
                exit_reason = "profit_target"
        if exit_val is None:
            if x_sp_a is None or x_sc_a is None or pd.isna(x_sp_a) or pd.isna(x_sc_a):
                rows.append(dict(zip(TRADE_COLS, [ticker, td, td.strftime("%a"), name,
                                                  round(spot, 2), sp, sc, lp, lc,
                                                  round(sp - lp, 2), round(credit, 3),
                                                  "no_exit_quotes", None])))
                continue
            lp_b_s = x_lp_b if (x_lp_b is not None and not pd.isna(x_lp_b)) else 0.0
            lc_b_s = x_lc_b if (x_lc_b is not None and not pd.isna(x_lc_b)) else 0.0
            if use_mid:
                sp_m = (x_sp_b + x_sp_a) / 2 if (x_sp_b is not None and not pd.isna(x_sp_b)) else x_sp_a
                sc_m = (x_sc_b + x_sc_a) / 2 if (x_sc_b is not None and not pd.isna(x_sc_b)) else x_sc_a
                lp_m = (lp_b_s + (x_lp_a if (x_lp_a is not None and not pd.isna(x_lp_a)) else lp_b_s)) / 2
                lc_m = (lc_b_s + (x_lc_a if (x_lc_a is not None and not pd.isna(x_lc_a)) else lc_b_s)) / 2
                exit_val = sp_m + sc_m - lp_m - lc_m + slip
            else:
                exit_val = x_sp_a + x_sc_a - lp_b_s - lc_b_s + slip
            exit_reason = "forced_close"
        pnl = (credit - exit_val) * 100
        rows.append(dict(zip(TRADE_COLS, [ticker, td, td.strftime("%a"), name,
                                          round(spot, 2), sp, sc, lp, lc,
                                          round(sp - lp, 2), round(credit, 3),
                                          exit_reason, round(pnl, 2)])))
    return rows, "ok"


def append_rows(path, rows, cols):
    new = not path.exists()
    pd.DataFrame(rows, columns=cols).to_csv(path, mode="a", header=new, index=False)


def run_ticker(ticker):
    data_dir, pattern, spacing = TICKERS[ticker]
    final = OUT / f"trades_{ticker}.csv"
    if final.exists():
        log(f"[{ticker}] trades_{ticker}.csv exists — SKIP (already done)")
        return
    partial = OUT / f"trades_{ticker}.partial.csv"
    skips = OUT / f"skips_{ticker}.csv"
    done_dates = set()
    if partial.exists():
        try:
            done_dates |= set(pd.read_csv(partial)["trade_date"].astype(str))
        except Exception:
            pass
    if skips.exists():
        try:
            done_dates |= set(pd.read_csv(skips)["trade_date"].astype(str))
        except Exception:
            pass
    files = sorted(data_dir.glob(pattern))
    log(f"[{ticker}] {len(files)} day files, {len(done_dates)} already processed, "
        f"spacing=${spacing}")
    t0 = time.time()
    n_done = 0
    for fp in files:
        ds = fp.stem.split("_")[-1]
        td = date(int(ds[:4]), int(ds[4:6]), int(ds[6:8]))
        if str(td) in done_dates:
            continue
        if td.weekday() not in (0, 2, 4):
            # cheap skip without reading the file; still record it
            append_rows(skips, [{"ticker": ticker, "trade_date": td,
                                 "status": "not_mwf"}], SKIP_COLS)
            continue
        try:
            rows, status = process_day(ticker, fp, td, spacing)
        except Exception as e:  # noqa: BLE001
            rows, status = [], f"error:{type(e).__name__}"
        if rows:
            append_rows(partial, rows, TRADE_COLS)
        else:
            append_rows(skips, [{"ticker": ticker, "trade_date": td,
                                 "status": status}], SKIP_COLS)
        n_done += 1
        if n_done % 50 == 0:
            log(f"[{ticker}]   ...{n_done} days processed ({time.time()-t0:.0f}s)")
    # finalize atomically
    if partial.exists():
        tmp = OUT / f".trades_{ticker}.tmp.csv"
        pd.read_csv(partial).to_csv(tmp, index=False)
        os.replace(tmp, final)
    else:
        # no trades at all — write an empty marker file so restart skips
        tmp = OUT / f".trades_{ticker}.tmp.csv"
        pd.DataFrame(columns=TRADE_COLS).to_csv(tmp, index=False)
        os.replace(tmp, final)
    log(f"[{ticker}] DONE in {time.time()-t0:.0f}s -> {final.name}")


if __name__ == "__main__":
    order = sys.argv[1:] if len(sys.argv) > 1 else list(TICKERS)
    for t in order:
        run_ticker(t)
    log("ALL TICKERS COMPLETE")
