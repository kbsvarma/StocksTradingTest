"""Deep validation of IC 0.5% OTM / 25pt wings strategy.

Tests:
1. Train/test split (first 6 weeks vs last 6 weeks)
2. Bootstrap confidence interval on win rate and avg P&L
3. Conditional rules: skip after loss, skip after big winner, prior-day direction
4. Per-day-of-week optimization (tighter Mon/Wed, wider Fri)
5. Multi-exit-time variants
6. Slippage sensitivity (what if real fills are 20% worse?)
7. Drawdown-based position sizing
"""
import re
import time
import random
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
import pandas as pd
import numpy as np
import yfinance as yf

DATA_DIR = Path(__file__).parent / "data"
ET = ZoneInfo("America/New_York")
UTC = timezone.utc
SYM_RE = re.compile(r"^([A-Z]+)\s+(\d{6})([PC])(\d{8})$")

def log(m): print(m, flush=True)

log("Loading 2026 parquets...")
t0 = time.time()
expiry_index = defaultdict(list)
strikes_by_day = defaultdict(lambda: defaultdict(set))
for f in sorted(DATA_DIR.glob("avgo_cbbo_2026*.parquet")):
    df = pd.read_parquet(f)[["bid_px_00", "ask_px_00", "symbol"]]
    df["sym_clean"] = df["symbol"].str.strip()
    ext = df["sym_clean"].str.extract(SYM_RE)
    ext.columns = ["root", "yymmdd", "right", "strike_str"]
    df["expiry"] = pd.to_datetime(ext["yymmdd"], format="%y%m%d").dt.date
    df["right"] = ext["right"]
    df["strike"] = ext["strike_str"].astype(float) / 1000
    df = df.dropna(subset=["expiry", "right", "strike"])
    ds = f.stem.replace("avgo_cbbo_", "")
    td = date(int(ds[:4]), int(ds[4:6]), int(ds[6:8]))
    for (exp, strike, right), grp in df.groupby(["expiry", "strike", "right"]):
        expiry_index[(exp, strike, right)].append(grp[["bid_px_00", "ask_px_00"]])
        strikes_by_day[td][exp].add(strike)
for k in list(expiry_index.keys()):
    expiry_index[k] = pd.concat(expiry_index[k]).sort_index()

spot = yf.Ticker("AVGO").history(start="2026-02-01", end="2026-06-01")
spot.index = pd.to_datetime(spot.index).tz_localize(None)
spot_by_date = {pd.Timestamp(d).date(): row for d, row in spot.iterrows()}
log(f"Loaded ({time.time()-t0:.0f}s)")


def find_strike(avail, target, spacing=2.5):
    snapped = round(target / spacing) * spacing
    if snapped in avail: return snapped
    for d in [2.5, 5, 7.5, 10, 12.5, 15]:
        for sign in [+1, -1]:
            t = snapped + sign * d
            if t in avail: return t
    return None


def at_or_after(df, ts):
    if df is None or df.empty: return None
    sub = df.loc[df.index >= ts]
    return None if sub.empty else sub.iloc[0]


def simulate_ic(td, otm_put, otm_call, wing_put, wing_call,
                spot_entry, spot_exp_close, entry_ts, exit_ts,
                stop_mult=2.0, pt_frac=0.5):
    avail = strikes_by_day[td].get(td, set())
    if not avail: return None
    sp = find_strike(avail, spot_entry * (1 - otm_put))
    sc = find_strike(avail, spot_entry * (1 + otm_call))
    if sp is None or sc is None: return None
    lp = find_strike(avail, sp - wing_put)
    lc = find_strike(avail, sc + wing_call)
    if lp is None or lc is None: return None

    sp_df = expiry_index.get((td, sp, "P"))
    sc_df = expiry_index.get((td, sc, "C"))
    lp_df = expiry_index.get((td, lp, "P"))
    lc_df = expiry_index.get((td, lc, "C"))
    if any(x is None for x in [sp_df, sc_df, lp_df, lc_df]): return None

    e_sp = at_or_after(sp_df, entry_ts); e_sc = at_or_after(sc_df, entry_ts)
    e_lp = at_or_after(lp_df, entry_ts); e_lc = at_or_after(lc_df, entry_ts)
    if any(x is None for x in [e_sp, e_sc, e_lp, e_lc]): return None

    credit = float(e_sp["bid_px_00"]) + float(e_sc["bid_px_00"]) - float(e_lp["ask_px_00"]) - float(e_lc["ask_px_00"])
    if credit < 0.15: return None

    stop_debit = credit * stop_mult
    pt_debit = credit * (1 - pt_frac)

    sp_w = sp_df.loc[(sp_df.index >= entry_ts) & (sp_df.index <= exit_ts)]
    sc_w = sc_df.loc[(sc_df.index >= entry_ts) & (sc_df.index <= exit_ts)]
    lp_w = lp_df.loc[(lp_df.index >= entry_ts) & (lp_df.index <= exit_ts)]
    lc_w = lc_df.loc[(lc_df.index >= entry_ts) & (lc_df.index <= exit_ts)]

    exit_val = None; exit_reason = None
    if not any(s.empty for s in [sp_w, sc_w, lp_w, lc_w]):
        sp_r = sp_w.rename(columns={"bid_px_00": "b_sp", "ask_px_00": "a_sp"})
        sc_r = sc_w.rename(columns={"bid_px_00": "b_sc", "ask_px_00": "a_sc"})
        lp_r = lp_w.rename(columns={"bid_px_00": "b_lp", "ask_px_00": "a_lp"})
        lc_r = lc_w.rename(columns={"bid_px_00": "b_lc", "ask_px_00": "a_lc"})
        al = sp_r.join(sc_r, how="inner").join(lp_r, how="inner").join(lc_r, how="inner")
        if not al.empty:
            al["debit"] = al["a_sp"] + al["a_sc"] - al["b_lp"] - al["b_lc"]
            sl = al[al["debit"] >= stop_debit]
            pt = al[al["debit"] <= pt_debit]
            sl_ts = sl.index.min() if not sl.empty else None
            pt_ts = pt.index.min() if not pt.empty else None
            if sl_ts is not None and (pt_ts is None or sl_ts < pt_ts):
                exit_val = al.loc[sl_ts, "debit"]; exit_reason = "stop_loss"
            elif pt_ts is not None:
                exit_val = al.loc[pt_ts, "debit"]; exit_reason = "profit_target"

    if exit_val is None:
        sp_int = max(0, sp - spot_exp_close); lp_int = max(0, lp - spot_exp_close)
        sc_int = max(0, spot_exp_close - sc); lc_int = max(0, spot_exp_close - lc)
        exit_val = sp_int + sc_int - lp_int - lc_int
        exit_reason = "expiry"

    pnl = (credit - exit_val) * 100
    return {
        "trade_date": td, "dow": td.strftime("%a"),
        "credit": credit, "credit_dollars": credit * 100,
        "exit_reason": exit_reason, "pnl": pnl,
        "sp_k": sp, "sc_k": sc, "lp_k": lp, "lc_k": lc,
        "spot_open": float(spot_by_date[td]["Open"]),
        "spot_close": spot_exp_close,
    }


def run(otm_put=0.005, otm_call=0.005, wing_put=25, wing_call=25,
        entry_hour=9, entry_min=45, exit_hour=15, exit_min=45,
        stop_mult=2.0, pt_frac=0.5, dow_filter=None):
    trades = []
    for td in sorted(strikes_by_day.keys()):
        if td.weekday() not in (0, 2, 4): continue
        if dow_filter and td.strftime("%a") not in dow_filter: continue
        if td not in spot_by_date: continue
        if td not in strikes_by_day[td]: continue
        spot_row = spot_by_date[td]
        spot_o = float(spot_row["Open"]); spot_c = float(spot_row["Close"])
        spot_entry = spot_o + 0.3 * (spot_c - spot_o)
        entry_ts = pd.Timestamp(datetime.combine(td, datetime.min.time(), tzinfo=ET).replace(
            hour=entry_hour, minute=entry_min).astimezone(UTC))
        exit_ts = pd.Timestamp(datetime.combine(td, datetime.min.time(), tzinfo=ET).replace(
            hour=exit_hour, minute=exit_min).astimezone(UTC))
        r = simulate_ic(td, otm_put, otm_call, wing_put, wing_call,
                        spot_entry, spot_c, entry_ts, exit_ts, stop_mult, pt_frac)
        if r: trades.append(r)
    return pd.DataFrame(trades) if trades else pd.DataFrame()


def summarize(df, label=""):
    if df.empty: return None
    n = len(df); wins = (df["pnl"] > 0).sum()
    return {
        "label": label, "n": n,
        "win_rate": wins/n*100,
        "total": df["pnl"].sum(),
        "avg": df["pnl"].mean(),
        "best": df["pnl"].max(),
        "worst": df["pnl"].min(),
    }


# ─── 1. BASELINE for reference ─────────────────────────────────────────────
log("\n" + "=" * 100)
log("BASELINE: IC 0.5% OTM / 25pt wings / 09:45-15:45 / 2.0x SL / 50% PT")
log("=" * 100)
baseline_df = run()
b = summarize(baseline_df, "baseline")
log(f"  N={b['n']}, Win={b['win_rate']:.0f}%, Total=${b['total']:+,.0f}, Avg=${b['avg']:+.0f}, Best=${b['best']:+.0f}, Worst=${b['worst']:+.0f}")


# ─── 2. TRAIN/TEST SPLIT ───────────────────────────────────────────────────
log("\n" + "=" * 100)
log("2. TRAIN/TEST SPLIT — does the edge hold out-of-sample?")
log("=" * 100)
baseline_df = baseline_df.copy()
baseline_df["trade_date"] = pd.to_datetime(baseline_df["trade_date"])
baseline_df = baseline_df.sort_values("trade_date").reset_index(drop=True)
mid = len(baseline_df) // 2
train = baseline_df.iloc[:mid]
test = baseline_df.iloc[mid:]
log(f"\nTRAIN (first {mid} trades, {train['trade_date'].min().date()} → {train['trade_date'].max().date()}):")
ts = summarize(train); log(f"  N={ts['n']}, Win={ts['win_rate']:.0f}%, Total=${ts['total']:+,.0f}, Avg=${ts['avg']:+.0f}")
log(f"TEST  (last {len(test)} trades, {test['trade_date'].min().date()} → {test['trade_date'].max().date()}):")
ts2 = summarize(test); log(f"  N={ts2['n']}, Win={ts2['win_rate']:.0f}%, Total=${ts2['total']:+,.0f}, Avg=${ts2['avg']:+.0f}")
log(f"\nDoes test confirm train? Train/test P&L ratio: {ts['total']/(ts2['total']+0.01):.2f}")


# ─── 3. BOOTSTRAP CONFIDENCE INTERVAL ──────────────────────────────────────
log("\n" + "=" * 100)
log("3. BOOTSTRAP CONFIDENCE INTERVAL (1000 resamples)")
log("=" * 100)
random.seed(42)
n_trades = len(baseline_df)
n_resamples = 1000
win_rates = []
avg_pnls = []
totals = []
pnls = baseline_df["pnl"].values
for _ in range(n_resamples):
    sample = np.random.choice(pnls, size=n_trades, replace=True)
    win_rates.append((sample > 0).mean() * 100)
    avg_pnls.append(sample.mean())
    totals.append(sample.sum())
win_rates = np.array(win_rates); avg_pnls = np.array(avg_pnls); totals = np.array(totals)
log(f"  Win rate:  {np.percentile(win_rates, 2.5):.1f}% — {np.percentile(win_rates, 97.5):.1f}%  (95% CI)")
log(f"  Avg/trade: ${np.percentile(avg_pnls, 2.5):+.0f} — ${np.percentile(avg_pnls, 97.5):+.0f}")
log(f"  Total P&L: ${np.percentile(totals, 2.5):+,.0f} — ${np.percentile(totals, 97.5):+,.0f}")
log(f"  Probability total > 0: {(totals > 0).mean()*100:.1f}%")
log(f"  Probability win rate > 50%: {(win_rates > 50).mean()*100:.1f}%")


# ─── 4. PER DAY-OF-WEEK OPTIMIZATION ───────────────────────────────────────
log("\n" + "=" * 100)
log("4. PER-DAY OPTIMIZATION (does Friday want different config?)")
log("=" * 100)
log(f"{'Day':<6}{'BestOTM':<10}{'BestWing':<10}{'N':<5}{'Win%':<7}{'Total':<11}{'Avg':<9}")
log("-" * 70)
best_per_day = {}
for dow in ["Mon", "Wed", "Fri"]:
    best = None
    for otm in [0.005, 0.01, 0.015, 0.02, 0.03]:
        for w in [15, 25, 40]:
            df = run(otm_put=otm, otm_call=otm, wing_put=w, wing_call=w, dow_filter=[dow])
            if df.empty: continue
            s = summarize(df)
            if best is None or s["total"] > best["total"]:
                best = {**s, "otm": otm, "wing": w}
    best_per_day[dow] = best
    if best:
        log(f"{dow:<6}{best['otm']*100:<9.1f}%{best['wing']:<10}{best['n']:<5}{best['win_rate']:<6.0f}%${best['total']:<+10,.0f}${best['avg']:<+8.0f}")

# Combined: use best per-day vs baseline
combined_total = sum(b["total"] for b in best_per_day.values() if b)
log(f"\nCombined per-day-optimized total: ${combined_total:+,.0f}")
log(f"Baseline (single config all days):  ${b['total']:+,.0f}")
log(f"Improvement (could be overfit):     ${combined_total - b['total']:+,.0f}")


# ─── 5. CONDITIONAL RULES ──────────────────────────────────────────────────
log("\n" + "=" * 100)
log("5. CONDITIONAL RULES — skip after loss? skip after big win?")
log("=" * 100)

def rule_skip_after_loss(df):
    """Skip the day after a losing trade."""
    df = df.copy().sort_values("trade_date").reset_index(drop=True)
    df["prev_pnl"] = df["pnl"].shift(1)
    df["skip"] = (df["prev_pnl"] <= 0) & df["prev_pnl"].notna()
    return df

def rule_skip_after_big_win(df, threshold=400):
    df = df.copy().sort_values("trade_date").reset_index(drop=True)
    df["prev_pnl"] = df["pnl"].shift(1)
    df["skip"] = (df["prev_pnl"] >= threshold) & df["prev_pnl"].notna()
    return df

def rule_skip_friday(df):
    df = df.copy()
    df["skip"] = df["dow"] == "Fri"
    return df

def rule_skip_monday(df):
    df = df.copy()
    df["skip"] = df["dow"] == "Mon"
    return df

def rule_consecutive_losses(df, n=2):
    """Skip if 2+ consecutive losses."""
    df = df.copy().sort_values("trade_date").reset_index(drop=True)
    df["loss_streak"] = 0
    streak = 0
    for i, r in df.iterrows():
        if i == 0:
            df.at[i, "skip"] = False
            streak = 1 if r["pnl"] <= 0 else 0
            continue
        df.at[i, "skip"] = streak >= n
        if r["pnl"] <= 0: streak += 1
        else: streak = 0
    df["skip"] = df["skip"].astype(bool)
    return df

rules = {
    "Skip after loss": rule_skip_after_loss,
    "Skip after big win (>$400)": rule_skip_after_big_win,
    "Skip Friday": rule_skip_friday,
    "Skip Monday": rule_skip_monday,
    "Skip after 2+ consecutive losses": rule_consecutive_losses,
}

log(f"{'Rule':<38}{'Trades':<10}{'Win%':<8}{'Total':<11}{'Avg':<9}{'vs Baseline':<12}")
log("-" * 90)
log(f"{'NO RULE (baseline)':<38}{b['n']:<10}{b['win_rate']:<7.0f}%${b['total']:<+10,.0f}${b['avg']:<+8.0f}+$0")
for rule_name, rule_fn in rules.items():
    df = rule_fn(baseline_df)
    kept = df[~df["skip"]]
    if len(kept) == 0:
        continue
    s = summarize(kept)
    delta = s["total"] - b["total"]
    log(f"{rule_name:<38}{s['n']:<10}{s['win_rate']:<7.0f}%${s['total']:<+10,.0f}${s['avg']:<+8.0f}${delta:<+11,.0f}")


# ─── 6. AVGO PRIOR-DAY DIRECTION FILTER ────────────────────────────────────
log("\n" + "=" * 100)
log("6. AVGO PRIOR-DAY DIRECTION as filter")
log("=" * 100)
# Get prior trading day's close-to-close for each trade date
all_dates = sorted(spot_by_date.keys())
for col_name, condition in [("after_up_day", lambda r: r > 0),
                            ("after_down_day", lambda r: r < 0),
                            ("after_big_up_>2%", lambda r: r > 2),
                            ("after_big_down_<-2%", lambda r: r < -2)]:
    keep_dates = []
    for td in baseline_df["trade_date"].dt.date:
        # Find prior trading day
        prior = None
        for d in reversed(all_dates):
            if d < td:
                prior = d
                break
        if prior is None: continue
        prior_close = float(spot_by_date[prior]["Close"])
        prior_open = float(spot_by_date[prior]["Open"])
        prior_move = (prior_close - prior_open) / prior_open * 100
        if condition(prior_move):
            keep_dates.append(td)
    sub = baseline_df[baseline_df["trade_date"].dt.date.isin(keep_dates)]
    if len(sub) > 0:
        s = summarize(sub)
        log(f"{col_name:<30}{s['n']:<5}trades  Win={s['win_rate']:.0f}%  Total=${s['total']:+,.0f}  Avg=${s['avg']:+.0f}")


# ─── 7. SLIPPAGE SENSITIVITY ───────────────────────────────────────────────
log("\n" + "=" * 100)
log("7. SLIPPAGE SENSITIVITY — what if real fills are X% worse than backtest?")
log("=" * 100)
for slip in [0, 0.05, 0.10, 0.15, 0.20, 0.30]:
    # Reduce credit by slip × credit (entry slippage)
    # And increase exit cost by slip × credit (exit slippage)
    # So total P&L impact = -2 × slip × credit per trade
    avg_credit = baseline_df["credit_dollars"].mean()
    slippage_cost = 2 * slip * avg_credit
    adjusted_total = baseline_df["pnl"].sum() - slippage_cost * len(baseline_df)
    adjusted_avg = adjusted_total / len(baseline_df)
    adj_wins = (baseline_df["pnl"] - slippage_cost > 0).sum()
    log(f"  Slippage {slip*100:.0f}%: Total=${adjusted_total:+,.0f}  Avg=${adjusted_avg:+.0f}  Win%={adj_wins/len(baseline_df)*100:.0f}%")


# ─── 8. EXIT TIME SENSITIVITY ──────────────────────────────────────────────
log("\n" + "=" * 100)
log("8. EXIT TIME SENSITIVITY — what if we close earlier?")
log("=" * 100)
log(f"{'Exit':<8}{'N':<5}{'Win%':<7}{'Total':<11}{'Avg':<9}{'Best':<9}{'Worst':<9}")
log("-" * 70)
for h, m in [(12, 0), (13, 0), (13, 30), (14, 0), (14, 30), (15, 0), (15, 15), (15, 30), (15, 45), (16, 0)]:
    df = run(exit_hour=h, exit_min=m)
    if df.empty: continue
    s = summarize(df)
    log(f"{h:02d}:{m:02d}    {s['n']:<5}{s['win_rate']:<6.0f}%${s['total']:<+10,.0f}${s['avg']:<+8.0f}${s['best']:<+8.0f}${s['worst']:<+8.0f}")


# ─── 9. POSITION SIZING RULES ──────────────────────────────────────────────
log("\n" + "=" * 100)
log("9. POSITION SIZING — fixed vs adaptive")
log("=" * 100)
sorted_df = baseline_df.copy().sort_values("trade_date").reset_index(drop=True)

# Fixed 1 contract: baseline total
fixed_total = sorted_df["pnl"].sum()
log(f"Fixed 1 contract:           Total=${fixed_total:+,.0f}")

# Anti-martingale: 1 contract normally, 2 after a win
def anti_martingale(df):
    contracts = [1] * len(df)
    for i in range(1, len(df)):
        if df.iloc[i-1]["pnl"] > 0:
            contracts[i] = 2
    df = df.copy()
    df["contracts"] = contracts
    df["adj_pnl"] = df["pnl"] * df["contracts"]
    return df

am = anti_martingale(sorted_df)
log(f"Anti-martingale (2 after win): Total=${am['adj_pnl'].sum():+,.0f}  (uses 2x capital after wins)")

# Cut size in half after loss
def reduce_after_loss(df):
    contracts = [1.0] * len(df)
    for i in range(1, len(df)):
        if df.iloc[i-1]["pnl"] <= 0:
            contracts[i] = 0.5
    df = df.copy()
    df["contracts"] = contracts
    df["adj_pnl"] = df["pnl"] * df["contracts"]
    return df

rl = reduce_after_loss(sorted_df)
log(f"Half-size after loss:         Total=${rl['adj_pnl'].sum():+,.0f}  (effectively reduces exposure on bad days)")

# Drawdown-based sizing: cut to half if current drawdown > $1000
def drawdown_sizing(df):
    df = df.copy()
    df["cum"] = df["pnl"].cumsum()
    df["peak"] = df["cum"].cummax()
    df["dd"] = df["cum"] - df["peak"]
    contracts = []
    for i, r in df.iterrows():
        if i == 0 or df.iloc[i-1]["dd"] >= -1000:
            contracts.append(1)
        else:
            contracts.append(0.5)  # half size after drawdown
    df["contracts"] = contracts
    df["adj_pnl"] = df["pnl"] * df["contracts"]
    return df

dds = drawdown_sizing(sorted_df)
log(f"Reduce to 0.5x if DD>$1000:   Total=${dds['adj_pnl'].sum():+,.0f}")

log("\nNote: any sizing change that worked on 33 trades is HIGHLY susceptible to curve-fitting.")
