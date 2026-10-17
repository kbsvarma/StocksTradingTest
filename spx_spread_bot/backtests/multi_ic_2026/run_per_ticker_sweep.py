"""Per-ticker strategy sweep on multi-ticker 2026 data.

For each ticker (NVDA, META, AAPL, MSFT, GOOGL, AMZN, TSLA):
  - IC at 0.5%, 1%, 2%, 3% OTM × wing 4%, 6%, 8% (% of spot)
  - Iron Fly at various wings (% of spot)
  - BPS only
  - BCS only
  - Asymmetric IC (1%P/2%C, 1%P/3%C, 2%P/1%C)

All at 09:45 ET entry, 15:45 ET forced close.
Find best risk-adjusted config per ticker.
"""
import re
import time
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
import pandas as pd
import yfinance as yf

ROOT = Path(__file__).parent / "data"
ET = ZoneInfo("America/New_York")
UTC = timezone.utc
SYM_RE = re.compile(r"^([A-Z]+)\s+(\d{6})([PC])(\d{8})$")

TICKER_CFG = {
    "NVDA":  {"spacing": 1.0,  "dir": ROOT / "nvda"},
    "META":  {"spacing": 5.0,  "dir": ROOT / "meta"},
    "AAPL":  {"spacing": 2.5,  "dir": ROOT / "aapl"},
    "MSFT":  {"spacing": 5.0,  "dir": ROOT / "msft"},
    "GOOGL": {"spacing": 2.5,  "dir": ROOT / "googl"},
    "AMZN":  {"spacing": 2.5,  "dir": ROOT / "amzn"},
    "TSLA":  {"spacing": 5.0,  "dir": ROOT / "tsla"},
}

def log(m): print(m, flush=True)


def load_ticker(ticker):
    cfg = TICKER_CFG[ticker]
    expiry_index = {}
    strikes_by_day = defaultdict(lambda: defaultdict(set))
    for f in sorted(cfg["dir"].glob(f"{ticker.lower()}_cbbo_*.parquet")):
        df = pd.read_parquet(f)[["bid_px_00", "ask_px_00", "symbol"]]
        df["sym_clean"] = df["symbol"].str.strip()
        ext = df["sym_clean"].str.extract(SYM_RE)
        ext.columns = ["root", "yymmdd", "right", "strike_str"]
        df["expiry"] = pd.to_datetime(ext["yymmdd"], format="%y%m%d").dt.date
        df["right"] = ext["right"]
        df["strike"] = ext["strike_str"].astype(float) / 1000
        df = df.dropna(subset=["expiry", "right", "strike"])
        ds = f.stem.split("_")[-1]
        td = date(int(ds[:4]), int(ds[4:6]), int(ds[6:8]))
        for (exp, strike, right), grp in df.groupby(["expiry", "strike", "right"]):
            expiry_index[(td, exp, strike, right)] = grp[["bid_px_00", "ask_px_00"]]
            strikes_by_day[td][exp].add(strike)
    return expiry_index, strikes_by_day, cfg["spacing"]


def find_strike(avail, target, spacing):
    snapped = round(target / spacing) * spacing
    if snapped in avail: return snapped
    for d_mult in range(1, 12):
        for sign in [+1, -1]:
            t = snapped + sign * d_mult * spacing
            if t in avail: return t
    return None


def at_or_after(df, ts):
    if df is None or df.empty: return None
    sub = df.loc[df.index >= ts]
    return None if sub.empty else sub.iloc[0]


def simulate_legs(td, target_exp, legs, spot_exp_close, entry_ts, exit_ts, expiry_index,
                  stop_mult=2.0, pt_frac=0.5):
    """legs: list of (strike, right, side=+1long/-1short)"""
    leg_data = []
    credit = 0
    for (strike, right, side) in legs:
        leg_df = expiry_index.get((td, target_exp, strike, right))
        if leg_df is None: return None
        e = at_or_after(leg_df, entry_ts)
        if e is None: return None
        if side == -1:
            credit += float(e["bid_px_00"])
        else:
            credit -= float(e["ask_px_00"])
        leg_data.append({"strike": strike, "right": right, "side": side, "df": leg_df})

    if credit < 0.10: return None

    stop_debit = credit * stop_mult
    pt_debit = credit * (1 - pt_frac)

    aligned = None
    for i, l in enumerate(leg_data):
        win = l["df"].loc[(l["df"].index >= entry_ts) & (l["df"].index <= exit_ts)]
        if win.empty: return None
        ren = win.rename(columns={"bid_px_00": f"b_{i}", "ask_px_00": f"a_{i}"})
        aligned = ren if aligned is None else aligned.join(ren, how="inner")
    if aligned is None or aligned.empty: return None

    debit = pd.Series(0.0, index=aligned.index)
    for i, l in enumerate(leg_data):
        if l["side"] == -1: debit += aligned[f"a_{i}"]
        else: debit -= aligned[f"b_{i}"]

    sl = aligned[debit >= stop_debit]
    pt = aligned[debit <= pt_debit]
    sl_ts = sl.index.min() if not sl.empty else None
    pt_ts = pt.index.min() if not pt.empty else None
    exit_val = None; exit_reason = None
    if sl_ts is not None and (pt_ts is None or sl_ts < pt_ts):
        exit_val = debit.loc[sl_ts]; exit_reason = "stop_loss"
    elif pt_ts is not None:
        exit_val = debit.loc[pt_ts]; exit_reason = "profit_target"
    else:
        v = 0
        for l in leg_data:
            if l["right"] == "P": intrinsic = max(0, l["strike"] - spot_exp_close)
            else: intrinsic = max(0, spot_exp_close - l["strike"])
            v += (1 if l["side"] == -1 else -1) * intrinsic
        exit_val = v; exit_reason = "expiry"

    pnl = (credit - exit_val) * 100
    return {"credit": credit, "exit_reason": exit_reason, "pnl": pnl}


# Strategy builders
def build_ic(avail, spot, otm_put, otm_call, wing_pct, spacing):
    sp = find_strike(avail, spot * (1 - otm_put), spacing)
    sc = find_strike(avail, spot * (1 + otm_call), spacing)
    if sp is None or sc is None: return None
    wing_dollars = spot * wing_pct
    lp = find_strike(avail, sp - wing_dollars, spacing)
    lc = find_strike(avail, sc + wing_dollars, spacing)
    if lp is None or lc is None: return None
    return [(lp, "P", +1), (sp, "P", -1), (sc, "C", -1), (lc, "C", +1)]


def build_iron_fly(avail, spot, wing_pct, spacing):
    atm = find_strike(avail, spot, spacing)
    if atm is None: return None
    wing_dollars = spot * wing_pct
    lp = find_strike(avail, atm - wing_dollars, spacing)
    lc = find_strike(avail, atm + wing_dollars, spacing)
    if lp is None or lc is None: return None
    return [(lp, "P", +1), (atm, "P", -1), (atm, "C", -1), (lc, "C", +1)]


def build_bps(avail, spot, otm, wing_pct, spacing):
    sp = find_strike(avail, spot * (1 - otm), spacing)
    if sp is None: return None
    wing_dollars = spot * wing_pct
    lp = find_strike(avail, sp - wing_dollars, spacing)
    if lp is None: return None
    return [(lp, "P", +1), (sp, "P", -1)]


def build_bcs(avail, spot, otm, wing_pct, spacing):
    sc = find_strike(avail, spot * (1 + otm), spacing)
    if sc is None: return None
    wing_dollars = spot * wing_pct
    lc = find_strike(avail, sc + wing_dollars, spacing)
    if lc is None: return None
    return [(sc, "C", -1), (lc, "C", +1)]


def run_strategy_on_ticker(ticker, expiry_index, strikes_by_day, spacing,
                           spot_by_date, strategy_name, build_fn, **kwargs):
    trades = []
    for td in sorted(strikes_by_day.keys()):
        if td.weekday() not in (0, 2, 4): continue
        if td not in spot_by_date: continue
        if td not in strikes_by_day[td]: continue
        spot_row = spot_by_date[td]
        spot_o = float(spot_row["Open"]); spot_c = float(spot_row["Close"])
        spot_entry = spot_o + 0.3 * (spot_c - spot_o)

        avail = strikes_by_day[td][td]
        legs = build_fn(avail, spot_entry, spacing=spacing, **kwargs)
        if legs is None: continue
        entry_ts = pd.Timestamp(datetime.combine(td, datetime.min.time(), tzinfo=ET).replace(
            hour=9, minute=45).astimezone(UTC))
        exit_ts = pd.Timestamp(datetime.combine(td, datetime.min.time(), tzinfo=ET).replace(
            hour=15, minute=45).astimezone(UTC))
        r = simulate_legs(td, td, legs, spot_c, entry_ts, exit_ts, expiry_index)
        if r:
            r["ticker"] = ticker; r["trade_date"] = td; r["dow"] = td.strftime("%a")
            r["strategy"] = strategy_name
            r.update(kwargs)
            trades.append(r)
    return pd.DataFrame(trades)


def summarize(df):
    if df.empty: return None
    n = len(df); wins = (df["pnl"] > 0).sum()
    return {
        "n": n, "wins": wins, "win_rate": wins / n * 100,
        "avg_credit": df["credit"].mean() * 100,
        "total_pnl": df["pnl"].sum(),
        "avg_pnl": df["pnl"].mean(),
        "best": df["pnl"].max(), "worst": df["pnl"].min(),
        "stops": (df["exit_reason"] == "stop_loss").sum(),
    }


# ─── Main loop ──────────────────────────────────────────────────────────────
log("=" * 110)
log("PER-TICKER STRATEGY SWEEP — find best config for each ticker")
log("=" * 110)

# Strategy variants to test
ic_variants = []
for otm in [0.005, 0.01, 0.015, 0.02, 0.03]:
    for wp in [0.04, 0.06, 0.08]:
        ic_variants.append({"otm_put": otm, "otm_call": otm, "wing_pct": wp, "name": f"IC_{otm*100:.1f}%_{wp*100:.0f}%w"})

# Asymmetric IC: tilted
asym_variants = []
for op in [0.005, 0.01, 0.02]:
    for oc in [0.01, 0.02, 0.03, 0.04]:
        if op != oc:
            asym_variants.append({"otm_put": op, "otm_call": oc, "wing_pct": 0.06,
                                  "name": f"IC_P{op*100:.1f}%_C{oc*100:.1f}%_6%w"})

ifly_variants = [{"wing_pct": wp, "name": f"IFLY_{wp*100:.0f}%w"} for wp in [0.04, 0.06, 0.08, 0.10]]
bps_variants = [{"otm": o, "wing_pct": wp, "name": f"BPS_{o*100:.1f}%_{wp*100:.0f}%w"}
                for o in [0.005, 0.01, 0.02] for wp in [0.04, 0.06]]
bcs_variants = [{"otm": o, "wing_pct": wp, "name": f"BCS_{o*100:.1f}%_{wp*100:.0f}%w"}
                for o in [0.005, 0.01, 0.02] for wp in [0.04, 0.06]]

all_summary = []
ticker_best = {}

for ticker in ["NVDA", "META", "AAPL", "MSFT", "GOOGL", "AMZN", "TSLA"]:
    log(f"\n{'='*100}")
    log(f"TICKER: {ticker}")
    log(f"{'='*100}")

    t0 = time.time()
    expiry_index, strikes_by_day, spacing = load_ticker(ticker)
    spot_hist = yf.Ticker(ticker).history(start="2026-01-01", end="2026-06-01")
    spot_hist.index = pd.to_datetime(spot_hist.index).tz_localize(None)
    spot_by_date = {pd.Timestamp(d).date(): row for d, row in spot_hist.iterrows()}
    log(f"  Loaded ({time.time()-t0:.0f}s)")

    ticker_results = []

    # IC symmetric
    for v in ic_variants:
        df = run_strategy_on_ticker(ticker, expiry_index, strikes_by_day, spacing, spot_by_date,
                                     v["name"], build_ic, otm_put=v["otm_put"], otm_call=v["otm_call"],
                                     wing_pct=v["wing_pct"])
        s = summarize(df)
        if s:
            r = {"strategy": v["name"], **s}
            ticker_results.append(r)

    # IC asymmetric
    for v in asym_variants:
        df = run_strategy_on_ticker(ticker, expiry_index, strikes_by_day, spacing, spot_by_date,
                                     v["name"], build_ic, otm_put=v["otm_put"], otm_call=v["otm_call"],
                                     wing_pct=v["wing_pct"])
        s = summarize(df)
        if s:
            r = {"strategy": v["name"], **s}
            ticker_results.append(r)

    # Iron Fly
    for v in ifly_variants:
        df = run_strategy_on_ticker(ticker, expiry_index, strikes_by_day, spacing, spot_by_date,
                                     v["name"], build_iron_fly, wing_pct=v["wing_pct"])
        s = summarize(df)
        if s:
            r = {"strategy": v["name"], **s}
            ticker_results.append(r)

    # BPS
    for v in bps_variants:
        df = run_strategy_on_ticker(ticker, expiry_index, strikes_by_day, spacing, spot_by_date,
                                     v["name"], build_bps, otm=v["otm"], wing_pct=v["wing_pct"])
        s = summarize(df)
        if s:
            r = {"strategy": v["name"], **s}
            ticker_results.append(r)

    # BCS
    for v in bcs_variants:
        df = run_strategy_on_ticker(ticker, expiry_index, strikes_by_day, spacing, spot_by_date,
                                     v["name"], build_bcs, otm=v["otm"], wing_pct=v["wing_pct"])
        s = summarize(df)
        if s:
            r = {"strategy": v["name"], **s}
            ticker_results.append(r)

    res_df = pd.DataFrame(ticker_results)
    if res_df.empty:
        log("  no results")
        continue

    # Filter to N>=15 for credibility
    credible = res_df[res_df["n"] >= 15].copy()
    if credible.empty:
        credible = res_df  # fall back to all

    # Top 10 by total P&L
    top = credible.sort_values("total_pnl", ascending=False).head(8)
    log(f"\n  Top 8 strategies for {ticker} (N>=15):")
    log(f"  {'Strategy':<30}{'N':<5}{'Win%':<7}{'Total':<11}{'Avg':<9}{'Best':<9}{'Worst':<10}")
    log(f"  {'-'*85}")
    for _, r in top.iterrows():
        log(f"  {r['strategy']:<30}{int(r['n']):<5}{r['win_rate']:<6.0f}%${r['total_pnl']:<+10,.0f}${r['avg_pnl']:<+8.0f}${r['best']:<+8.0f}${r['worst']:<+9.0f}")

    # Best variant for this ticker
    best = top.iloc[0]
    ticker_best[ticker] = best
    log(f"\n  ⭐ BEST {ticker}: {best['strategy']}")

    # Add ticker label to summary
    for _, r in credible.iterrows():
        all_summary.append({"ticker": ticker, **r.to_dict()})

# ─── CROSS-TICKER FINAL SUMMARY ────────────────────────────────────────────
log("\n" + "=" * 110)
log("FINAL — best strategy per ticker")
log("=" * 110)
log(f"{'Ticker':<8}{'Best Strategy':<30}{'N':<5}{'Win%':<7}{'Total':<11}{'Avg/trade':<11}{'Worst':<10}")
log("-" * 90)
for ticker, best in ticker_best.items():
    log(f"{ticker:<8}{best['strategy']:<30}{int(best['n']):<5}{best['win_rate']:<6.0f}%${best['total_pnl']:<+10,.0f}${best['avg_pnl']:<+10.0f}${best['worst']:<+9.0f}")

# Total deployable basket
log(f"\n  Sum across all 7 tickers (best config each): ${sum(b['total_pnl'] for b in ticker_best.values()):+,.0f}")

# Save full
pd.DataFrame(all_summary).to_csv("/tmp/multi_ticker_full_sweep.csv", index=False)
log("\n  Saved /tmp/multi_ticker_full_sweep.csv")
