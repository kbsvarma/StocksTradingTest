"""Campaign-2 closeout runner — covered_call/ program consolidation.

Reuses the existing engines (imported, not rewritten) and writes one
consolidated results table. CRASH-RESILIENT:
  - each (ticker,strategy) unit appended to results.partial.csv as completed
  - on restart, units already present are skipped
  - finalized to results.csv via atomic os.replace; nothing deleted/truncated

Strategies & engines:
  dailyCC_otm{0,1,2}   backtest_cc_daily.backtest  (daily option bars of weekly
                       calls; premium = Mon close - $0.03/sh; overlay model)
  acctCC_otm{0,1,2}    account_sim.simulate        (full account wheel-style CC:
                       hold 100sh, rebuy after assignment, dividends)
  csp5 / cc5           backtest_csp_vs_cc.run      (weekly 5% OTM put/call, BID fills)
  wheel5               backtest_wheel.run          (CSP<->CC state machine, BID fills;
                       stdout parsed)
  atmCC_weekly         inline (same overlay model as dailyCC but sourced from
                       the csp_weekly Monday BID quotes at the ATM strike)
  putwrite (SPY)       backtest_putwrite.run       (monthly CSP, $25k acct)

Data: local DataBento parquets in covered_call/data/ + yfinance daily stock
history (network use limited to yfinance, per task rules).
"""
import io
import os
import re
import sys
import contextlib
from pathlib import Path

import numpy as np
import pandas as pd

CC = Path("/Users/varmakammili/Documents/GitHub/StocksTradingTest/spx_spread_bot/backtests/covered_call")
OUT = CC / "closeout_2026_07_03"
OUT.mkdir(exist_ok=True)
DATA = CC / "data"
sys.path.insert(0, str(CC))

import account_sim                    # noqa: E402
import backtest_cc_daily              # noqa: E402
import backtest_csp_vs_cc             # noqa: E402
import backtest_wheel                 # noqa: E402
import backtest_putwrite              # noqa: E402

PARTIAL = OUT / "results.partial.csv"
FINAL = OUT / "results.csv"

COLS = ["unit", "ticker", "strategy", "window", "n", "wr_pct", "net_pnl",
        "bh_pnl", "edge_vs_bh", "max_dd", "assign_pct", "data_source", "notes"]

DAILY_TICKERS = {"OXY": ("2024-01-01", "2026-05-22"),
                 "SOFI": ("2024-01-01", "2026-05-22"),
                 "WFC": ("2024-01-01", "2026-05-22"),
                 "XLF": ("2024-01-01", "2026-05-22"),
                 "T":   ("2015-01-01", "2025-12-31")}
WEEKLY_TICKERS = ["BAC", "MO", "OXY", "T", "VZ", "WFC", "GILD", "XLF"]


def log(m):
    print(m, flush=True)


def done_units():
    if PARTIAL.exists():
        try:
            return set(pd.read_csv(PARTIAL)["unit"].astype(str))
        except Exception:
            return set()
    return set()


def append(rows):
    new = not PARTIAL.exists()
    pd.DataFrame(rows, columns=COLS).to_csv(PARTIAL, mode="a", header=new, index=False)


def max_dd_dollars(equity):
    eq = np.asarray(equity, dtype=float)
    if len(eq) == 0:
        return None
    peak = np.maximum.accumulate(eq)
    return float((eq - peak).min())


# ── units ────────────────────────────────────────────────────────────────────
def unit_daily_cc(t, otm_steps):
    start, end = DAILY_TICKERS[t]
    grid = backtest_cc_daily.GRIDS.get(t, 1.0)
    df = backtest_cc_daily.backtest(t, otm_steps=otm_steps, grid=grid,
                                    start_str=start, end_str=end)
    if df.empty:
        return {"n": 0, "notes": "NO TRADES"}
    window = f"{df['mon'].min()}..{df['fri'].max()}"
    eq = df["cc_total"].cumsum()
    return {"window": window, "n": len(df),
            "wr_pct": round((df["cc_total"] > 0).mean() * 100, 1),
            "net_pnl": round(df["cc_total"].sum(), 0),
            "bh_pnl": round(df["bh_total"].sum(), 0),
            "edge_vs_bh": round(df["cc_total"].sum() - df["bh_total"].sum(), 0),
            "max_dd": round(max_dd_dollars(eq), 0),
            "assign_pct": round(df["assigned"].mean() * 100, 1),
            "data_source": "DataBento daily option bars (local) + yfinance stock",
            "notes": "overlay model; premium=Mon close-$0.03/sh"}


def unit_acct_cc(t, otm_steps):
    start, end = DAILY_TICKERS[t]
    r = account_sim.simulate(t, otm_steps, start, end)
    if r is None:
        return {"n": 0, "notes": "NO DATA"}
    lg = r["log"]
    window = f"{lg['mon'].iloc[0]}..{lg['fri'].iloc[-1]}"
    weekly_chg = lg["cc_value"].diff().dropna()
    return {"window": window, "n": int(r["n_weeks"]),
            "wr_pct": round((weekly_chg > 0).mean() * 100, 1),
            "net_pnl": round(r["cc_final"] - r["start_capital"], 0),
            "bh_pnl": round(r["bh_final"] - r["start_capital"], 0),
            "edge_vs_bh": round(r["cc_final"] - r["bh_final"], 0),
            "max_dd": round(max_dd_dollars(lg["cc_value"]), 0),
            "assign_pct": round(r["assignments"] / max(1, r["weeks_traded"]) * 100, 1),
            "data_source": "DataBento daily option bars (local) + yfinance stock+divs",
            "notes": f"full account sim incl divs; premium=close-$0.03/sh; "
                     f"weeks_traded={r['weeks_traded']}"}


def unit_csp_cc(t):
    r = backtest_csp_vs_cc.run(t, 0.05)
    if r is None:
        return None
    rows = []
    for strat, pnl, assign in (("csp5_weekly", r["csp_pnl"], r["csp_assign"]),
                               ("cc5_weekly", r["cc_pnl"], r["cc_assign"])):
        rows.append({"strategy": strat, "window": "2024-03..2026-05",
                     "n": r["n"], "wr_pct": None,
                     "net_pnl": round(pnl, 0), "bh_pnl": None, "edge_vs_bh": None,
                     "max_dd": None, "assign_pct": round(assign, 1),
                     "data_source": "DataBento Mon 09:35 quotes (local) + yfinance close",
                     "notes": "5% OTM, BID fills; option leg only (no B&H benchmark "
                              "in engine); skew memo: CSP prem "
                              f"${r['csp_prem']:,.0f} vs CC ${r['cc_prem']:,.0f}"})
    return rows


WHEEL_RX = {
    "weeks": re.compile(r"weeks traded:\s+(\d+)"),
    "assign": re.compile(r"assignments:\s+(\d+)\s+call-aways:\s+(\d+)"),
    "prem": re.compile(r"cum premium:\s+\$([+-][\d,]+)"),
    "wheel": re.compile(r"WHEEL total:\s+\$([+-][\d,]+)"),
    "bh": re.compile(r"Buy&Hold total:\s+\$([+-][\d,]+)"),
}


def unit_wheel(t):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        backtest_wheel.run(t, otm=0.05)
    out = buf.getvalue()
    (OUT / "logs").mkdir(exist_ok=True)
    logfp = OUT / "logs" / f"wheel_{t}.log"
    if not logfp.exists():
        logfp.write_text(out)
    def num(key, grp=1):
        m = WHEEL_RX[key].search(out)
        return float(m.group(grp).replace(",", "")) if m else None
    weeks = num("weeks")
    wheel_total = num("wheel")
    bh = num("bh")
    n_assign = num("assign", 1)
    n_call = num("assign", 2)
    # window from the data file itself
    df = pd.read_parquet(DATA / f"{t.lower()}_csp_weekly.parquet", columns=["monday"])
    window = f"{pd.to_datetime(df['monday']).min().date()}..{pd.to_datetime(df['monday']).max().date()}"
    return {"strategy": "wheel5_weekly", "window": window,
            "n": int(weeks) if weeks else None, "wr_pct": None,
            "net_pnl": round(wheel_total, 0) if wheel_total is not None else None,
            "bh_pnl": round(bh, 0) if bh is not None else None,
            "edge_vs_bh": round(wheel_total - bh, 0) if (wheel_total is not None and bh is not None) else None,
            "max_dd": None,
            "assign_pct": None,
            "data_source": "DataBento Mon 09:35 quotes (local) + yfinance close+divs",
            "notes": f"5% OTM CSP<->CC state machine, BID fills; "
                     f"assignments={n_assign:.0f} callaways={n_call:.0f}"}


def unit_atm_cc(t):
    """Weekly ATM covered-call overlay from the csp_weekly Monday BID quotes."""
    import yfinance as yf
    fp = DATA / f"{t.lower()}_csp_weekly.parquet"
    df = pd.read_parquet(fp)
    df = df[df["bid_px_00"] > 0].copy()
    rx = re.compile(r"\s(\d{6})([PC])(\d{8})$")
    parsed = df["symbol"].map(lambda s: rx.search(s.strip()))
    df = df[parsed.notna()].copy()
    df["side"] = [m.group(2) for m in parsed if m]
    df["strike"] = [int(m.group(3)) / 1000 for m in parsed if m]
    calls = df[df["side"] == "C"].copy()
    calls["monday"] = pd.to_datetime(calls["monday"])
    hist = yf.Ticker(t).history(start=str(calls["monday"].min().date()),
                                end="2026-05-23", auto_adjust=False)
    hist.index = pd.to_datetime(hist.index).tz_localize(None)
    rows = []
    for mon, g in calls.groupby("monday"):
        spot = float(g["spot"].iloc[0])
        fri = pd.Timestamp(g["friday"].iloc[0])
        g2 = g.drop_duplicates("strike").set_index("strike")["bid_px_00"]
        atm = min(g2.index, key=lambda k: abs(k - spot))
        prem = float(g2.loc[atm])
        fi = hist.index.get_indexer([fri], method="ffill")[0]
        if fi < 0:
            continue
        fclose = float(hist.iloc[fi]["Close"])
        cc_week = (min(fclose, atm) - spot) * 100 + prem * 100
        bh_week = (fclose - spot) * 100
        rows.append({"mon": mon, "cc": cc_week, "bh": bh_week,
                     "assigned": fclose > atm})
    w = pd.DataFrame(rows)
    if w.empty:
        return {"n": 0, "notes": "NO TRADES"}
    window = f"{w['mon'].min().date()}..{w['mon'].max().date()}"
    return {"strategy": "atmCC_weekly", "window": window, "n": len(w),
            "wr_pct": round((w["cc"] > 0).mean() * 100, 1),
            "net_pnl": round(w["cc"].sum(), 0),
            "bh_pnl": round(w["bh"].sum(), 0),
            "edge_vs_bh": round((w["cc"] - w["bh"]).sum(), 0),
            "max_dd": round(max_dd_dollars(w["cc"].cumsum()), 0),
            "assign_pct": round(w["assigned"].mean() * 100, 1),
            "data_source": "DataBento Mon 09:35 quotes (local) + yfinance close",
            "notes": "ATM strike, BID fills, overlay model (no divs both sides)"}


def unit_putwrite():
    lg, cap = backtest_putwrite.run(0.05)
    if lg.empty:
        return {"n": 0, "notes": "NO DATA"}
    window = f"{lg['entry'].iloc[0]}..{lg['expiry'].iloc[-1]}"
    monthly_net = lg["prem_income"] - lg["settle_loss"]
    return {"strategy": "putwrite5_monthly", "window": window, "n": len(lg),
            "wr_pct": round((monthly_net > 0).mean() * 100, 1),
            "net_pnl": round(lg["cash"].iloc[-1] - cap, 0),
            "bh_pnl": round(lg["bh_value"].iloc[-1] - cap, 0),
            "edge_vs_bh": round(lg["cash"].iloc[-1] - lg["bh_value"].iloc[-1], 0),
            "max_dd": round(max_dd_dollars(lg["cash"]), 0),
            "assign_pct": round(lg["assigned"].mean() * 100, 1),
            "data_source": "DataBento daily option bars (local) + yfinance SPY",
            "notes": "SPY 5% OTM monthly CSP, $25k acct, slip $0.05/sh, T-bill interest"}


def main():
    done = done_units()
    units = []
    for t in DAILY_TICKERS:
        for s in (0, 1, 2):
            units.append((f"dailyCC_{t}_otm{s}", lambda t=t, s=s: unit_daily_cc(t, s),
                          t, f"dailyCC_otm{s}"))
            units.append((f"acctCC_{t}_otm{s}", lambda t=t, s=s: unit_acct_cc(t, s),
                          t, f"acctCC_otm{s}"))
    for t in WEEKLY_TICKERS:
        units.append((f"cspcc_{t}", lambda t=t: unit_csp_cc(t), t, "csp5/cc5"))
        units.append((f"wheel_{t}", lambda t=t: unit_wheel(t), t, "wheel5_weekly"))
        units.append((f"atmcc_{t}", lambda t=t: unit_atm_cc(t), t, "atmCC_weekly"))
    units.append(("putwrite_SPY", unit_putwrite, "SPY", "putwrite5_monthly"))

    for uid, fn, ticker, strat in units:
        if uid in done:
            log(f"[skip] {uid} (already in results)")
            continue
        log(f"[run ] {uid} ...")
        try:
            res = fn()
        except FileNotFoundError as e:
            res = {"n": None, "notes": f"UNVERIFIABLE: missing local data ({e})"}
        except Exception as e:  # noqa: BLE001
            res = {"n": None, "notes": f"ERROR: {type(e).__name__}: {e}"}
        rows = res if isinstance(res, list) else [res]
        out_rows = []
        for r in rows:
            base = {c: None for c in COLS}
            base.update({"unit": uid, "ticker": ticker, "strategy": strat})
            base.update({k: v for k, v in r.items() if k in COLS})
            out_rows.append(base)
        append(out_rows)
        log(f"[done] {uid}")

    # finalize atomically
    tmp = OUT / ".results.tmp.csv"
    pd.read_csv(PARTIAL).to_csv(tmp, index=False)
    os.replace(tmp, FINAL)
    log("CAMPAIGN 2 COMPLETE -> results.csv")


if __name__ == "__main__":
    main()
