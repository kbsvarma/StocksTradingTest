"""
Overnight short-premium backtest on SPX options.

For each trading day N (with day N+1 data also present):
  - At day-N close: identify target contract(s) by DTE and OTM%.
  - "Sell" at day-N close mark.
  - At day-(N+1) open: "buy back" at day-(N+1) open mark
    for the SAME contract symbol (same strike, same expiry, same right).
  - Overnight P&L = (close_N - open_N+1) * 100 - slippage.

Variants:
  A. Short put,  DTE_at_sell in {1,2,3}, OTM in {0.25,0.50,1.00,1.50,2.00}%
  B. Short call, same grid
  C. Short straddle (ATM P + ATM C), DTE {1,2,3}
  D. Short strangle (0.5% OTM each side), DTE {1,2,3}
  E. Bull put spread (50pt) overnight, DTE {1,2,3}, OTM 0.75% (matches day bot)

Slippage: per-leg per-side, applied to "buy back" side only (we assume close
sale is at the mark; the unfavorable side is the next-day open we lift offer).
Two scenarios reported: optimistic ($0.05/leg) and realistic ($0.20/leg).
"""
from __future__ import annotations
import csv, json, warnings
from dataclasses import dataclass, asdict, field
from datetime import date, timedelta, datetime
from pathlib import Path
from typing import Optional, List, Dict, Tuple
import numpy as np
import pandas as pd

from load_data import load_all

warnings.filterwarnings("ignore")

OUT_DIR = Path(__file__).resolve().parent
SPX_CACHE = Path("/tmp/spx_daily_2014_2026.parquet")


def load_spx_daily() -> pd.DataFrame:
    if SPX_CACHE.exists():
        df = pd.read_parquet(SPX_CACHE)
    else:
        import yfinance as yf
        spx = yf.download("^GSPC", start="2014-01-01", end="2026-05-20",
                          auto_adjust=True, progress=False)
        vix = yf.download("^VIX", start="2014-01-01", end="2026-05-20",
                          auto_adjust=True, progress=False)
        for d_ in (spx, vix):
            if isinstance(d_.columns, pd.MultiIndex):
                d_.columns = [c[0] for c in d_.columns]
        df = spx[["Open", "High", "Low", "Close"]].copy()
        df.columns = ["spx_open", "spx_high", "spx_low", "spx_close"]
        df["vix_close"] = vix["Close"]
        df.index = pd.to_datetime(df.index).normalize()
        df.dropna(subset=["spx_open"], inplace=True)
        df.to_parquet(SPX_CACHE)
    df.index = pd.to_datetime(df.index).normalize()
    return df


def pick_strike_by_otm(opts: pd.DataFrame, spot: float, otm_pct: float, side: str) -> Optional[pd.Series]:
    """side='P' -> strike below spot (target = spot*(1-otm)); 'C' -> above."""
    if opts is None or opts.empty:
        return None
    if side == "P":
        target = spot * (1.0 - otm_pct)
    else:
        target = spot * (1.0 + otm_pct)
    o = opts.copy()
    o["dist"] = (o["strike"] - target).abs()
    row = o.loc[o["dist"].idxmin()]
    return row


def pick_strike_below(opts: pd.DataFrame, ref_strike: float, width: float) -> Optional[pd.Series]:
    """For BPS long leg: nearest strike at-or-below (ref_strike - width)."""
    if opts is None or opts.empty:
        return None
    target = ref_strike - width
    o = opts.copy()
    # prefer exact match else nearest
    exact = o[o["strike"] == target]
    if not exact.empty:
        return exact.iloc[0]
    below = o[o["strike"] < ref_strike]
    if below.empty:
        return None
    below = below.copy()
    below["dist"] = (below["strike"] - target).abs()
    return below.loc[below["dist"].idxmin()]


@dataclass
class Trade:
    entry_date: str
    next_date: str
    variant: str
    dte_at_sell: int
    otm_pct: float
    spx_close_N: float
    spx_open_N1: float
    overnight_pct: float
    short_strike: float
    long_strike: float  # NaN for non-spreads
    right: str          # 'P' / 'C' / 'PC' / 'BPS'
    credit_close: float
    debit_open: float
    n_legs: int
    pnl_opt: float       # $ before slippage
    pnl_real: float      # after $0.20/leg/side slippage on buyback
    pnl_optimistic: float # after $0.05/leg/side
    vix: float


def find_contract_next_day(raw: dict, next_day: date, right: str,
                           expiry: date, strike: float) -> Optional[pd.Series]:
    day_obj = raw.get(next_day)
    if day_obj is None:
        return None
    bucket = day_obj.get(right, {})
    df = bucket.get(expiry)
    if df is None or df.empty:
        return None
    match = df[df["strike"] == strike]
    if match.empty:
        return None
    return match.iloc[0]


def expiry_for_dte(day: date, dte: int) -> date:
    """Calendar-day expiry. Skip weekends -> push forward."""
    exp = day + timedelta(days=dte)
    while exp.weekday() >= 5:
        exp += timedelta(days=1)
    return exp


def simulate_single_leg(raw: dict, spx: pd.DataFrame, dte: int, otm_pct: float,
                        side: str) -> List[Trade]:
    """Sell single put or call at day-N close, buy back at day-N+1 open."""
    trades = []
    days = sorted(raw.keys())
    day_set = set(days)
    for i, day in enumerate(days[:-1]):
        next_day = days[i + 1]
        # require consecutive trading days
        ts = pd.Timestamp(day)
        ts1 = pd.Timestamp(next_day)
        if ts not in spx.index or ts1 not in spx.index:
            continue
        row = spx.loc[ts]
        row1 = spx.loc[ts1]
        spx_close = float(row["spx_close"])
        spx_open1 = float(row1["spx_open"])
        vix_val = float(row.get("vix_close", 20.0))
        if pd.isna(vix_val):
            vix_val = 20.0

        expiry = expiry_for_dte(day, dte)
        # Must not expire before next day's open
        if expiry <= day:
            continue
        # After overnight, remaining DTE >= 0
        if expiry < next_day:
            continue

        day_obj = raw.get(day, {})
        opts = day_obj.get(side, {}).get(expiry)
        if opts is None or opts.empty:
            continue

        short = pick_strike_by_otm(opts, spx_close, otm_pct, side)
        if short is None:
            continue
        strike = float(short["strike"])
        credit_close = float(short["close"])
        if credit_close <= 0.02:
            continue  # too cheap to be meaningful

        nxt = find_contract_next_day(raw, next_day, side, expiry, strike)
        if nxt is None:
            continue
        debit_open = float(nxt["open"])
        if debit_open < 0:
            continue

        pnl_opt = (credit_close - debit_open) * 100.0
        # buyback slippage: pay more
        pnl_real = (credit_close - (debit_open + 0.20)) * 100.0
        pnl_optimistic = (credit_close - (debit_open + 0.05)) * 100.0

        trades.append(Trade(
            entry_date=day.isoformat(),
            next_date=next_day.isoformat(),
            variant=f"short_{'put' if side=='P' else 'call'}",
            dte_at_sell=dte,
            otm_pct=otm_pct,
            spx_close_N=spx_close,
            spx_open_N1=spx_open1,
            overnight_pct=(spx_open1 / spx_close - 1) * 100,
            short_strike=strike,
            long_strike=float("nan"),
            right=side,
            credit_close=credit_close * 100,
            debit_open=debit_open * 100,
            n_legs=1,
            pnl_opt=pnl_opt,
            pnl_real=pnl_real,
            pnl_optimistic=pnl_optimistic,
            vix=vix_val,
        ))
    return trades


def simulate_straddle_or_strangle(raw, spx, dte, otm_pct, variant_name) -> List[Trade]:
    """Sell ATM (otm=0 -> straddle) or symmetric OTM (strangle) put+call."""
    trades = []
    days = sorted(raw.keys())
    for i, day in enumerate(days[:-1]):
        next_day = days[i + 1]
        ts = pd.Timestamp(day); ts1 = pd.Timestamp(next_day)
        if ts not in spx.index or ts1 not in spx.index:
            continue
        row = spx.loc[ts]; row1 = spx.loc[ts1]
        spx_close = float(row["spx_close"]); spx_open1 = float(row1["spx_open"])
        vix_val = float(row.get("vix_close", 20.0))
        if pd.isna(vix_val): vix_val = 20.0
        expiry = expiry_for_dte(day, dte)
        if expiry < next_day: continue
        day_obj = raw.get(day, {})
        puts = day_obj.get("P", {}).get(expiry)
        calls = day_obj.get("C", {}).get(expiry)
        if puts is None or calls is None: continue

        p = pick_strike_by_otm(puts, spx_close, otm_pct, "P")
        c = pick_strike_by_otm(calls, spx_close, otm_pct, "C")
        if p is None or c is None: continue
        p_strike = float(p["strike"]); c_strike = float(c["strike"])
        p_close = float(p["close"]); c_close = float(c["close"])
        credit_close = p_close + c_close
        if credit_close <= 0.10: continue

        p_nxt = find_contract_next_day(raw, next_day, "P", expiry, p_strike)
        c_nxt = find_contract_next_day(raw, next_day, "C", expiry, c_strike)
        if p_nxt is None or c_nxt is None: continue
        debit_open = float(p_nxt["open"]) + float(c_nxt["open"])

        pnl_opt = (credit_close - debit_open) * 100.0
        pnl_real = (credit_close - debit_open - 0.40) * 100.0  # 2 legs * 0.20
        pnl_optimistic = (credit_close - debit_open - 0.10) * 100.0  # 2 * 0.05

        trades.append(Trade(
            entry_date=day.isoformat(), next_date=next_day.isoformat(),
            variant=variant_name, dte_at_sell=dte, otm_pct=otm_pct,
            spx_close_N=spx_close, spx_open_N1=spx_open1,
            overnight_pct=(spx_open1/spx_close - 1)*100,
            short_strike=p_strike, long_strike=c_strike, right="PC",
            credit_close=credit_close*100, debit_open=debit_open*100,
            n_legs=2, pnl_opt=pnl_opt, pnl_real=pnl_real,
            pnl_optimistic=pnl_optimistic, vix=vix_val,
        ))
    return trades


def simulate_bps(raw, spx, dte, otm_pct, width=50) -> List[Trade]:
    """Bull put spread held overnight."""
    trades = []
    days = sorted(raw.keys())
    for i, day in enumerate(days[:-1]):
        next_day = days[i + 1]
        ts = pd.Timestamp(day); ts1 = pd.Timestamp(next_day)
        if ts not in spx.index or ts1 not in spx.index: continue
        row = spx.loc[ts]; row1 = spx.loc[ts1]
        spx_close = float(row["spx_close"]); spx_open1 = float(row1["spx_open"])
        vix_val = float(row.get("vix_close", 20.0))
        if pd.isna(vix_val): vix_val = 20.0
        expiry = expiry_for_dte(day, dte)
        if expiry < next_day: continue
        day_obj = raw.get(day, {})
        puts = day_obj.get("P", {}).get(expiry)
        if puts is None: continue
        short = pick_strike_by_otm(puts, spx_close, otm_pct, "P")
        if short is None: continue
        s_strike = float(short["strike"])
        long_row = pick_strike_below(puts, s_strike, width)
        if long_row is None: continue
        l_strike = float(long_row["strike"])
        s_close = float(short["close"]); l_close = float(long_row["close"])
        credit_close = s_close - l_close
        if credit_close <= 0.10: continue

        s_nxt = find_contract_next_day(raw, next_day, "P", expiry, s_strike)
        l_nxt = find_contract_next_day(raw, next_day, "P", expiry, l_strike)
        if s_nxt is None or l_nxt is None: continue
        s_open1 = float(s_nxt["open"]); l_open1 = float(l_nxt["open"])
        debit_open = s_open1 - l_open1

        pnl_opt = (credit_close - debit_open) * 100.0
        pnl_real = (credit_close - debit_open - 0.40) * 100.0
        pnl_optimistic = (credit_close - debit_open - 0.10) * 100.0

        trades.append(Trade(
            entry_date=day.isoformat(), next_date=next_day.isoformat(),
            variant="bps_overnight", dte_at_sell=dte, otm_pct=otm_pct,
            spx_close_N=spx_close, spx_open_N1=spx_open1,
            overnight_pct=(spx_open1/spx_close - 1)*100,
            short_strike=s_strike, long_strike=l_strike, right="BPS",
            credit_close=credit_close*100, debit_open=debit_open*100,
            n_legs=2, pnl_opt=pnl_opt, pnl_real=pnl_real,
            pnl_optimistic=pnl_optimistic, vix=vix_val,
        ))
    return trades


def stats(trades: List[Trade], pnl_field: str = "pnl_real") -> dict:
    if not trades: return dict(n=0)
    pnls = np.array([getattr(t, pnl_field) for t in trades])
    credits = np.array([t.credit_close for t in trades])
    n = len(trades)
    wins = (pnls > 0).sum()
    total = pnls.sum()
    avg = pnls.mean()
    std = pnls.std()
    sharpe = (avg / std * np.sqrt(252)) if std > 0 else 0.0
    avg_win = pnls[pnls > 0].mean() if (pnls > 0).any() else 0.0
    avg_loss = pnls[pnls <= 0].mean() if (pnls <= 0).any() else 0.0
    max_loss = pnls.min(); max_win = pnls.max()
    blowups_1k = int((pnls < -1000).sum())
    blowups_5k = int((pnls < -5000).sum())
    capture = np.where(credits > 0, (credits - pnls - credits) / credits, 0)
    # capture % = pnl/credit (how much premium we kept)
    cap_pct = np.where(credits > 0, pnls / credits * 100, 0).mean()
    first = datetime.fromisoformat(trades[0].entry_date).date()
    last = datetime.fromisoformat(trades[-1].entry_date).date()
    yrs = (last - first).days / 365.25
    # Pareto: top 5% nights contribute what % of (abs) pnl
    abs_pnl_sorted = np.sort(np.abs(pnls))[::-1]
    top5_share = abs_pnl_sorted[:max(1, n//20)].sum() / np.abs(pnls).sum() * 100 if np.abs(pnls).sum() > 0 else 0
    return dict(
        n=n, win_rate=wins/n*100, total_pnl=float(total), avg_pnl=float(avg),
        std_pnl=float(std), sharpe=float(sharpe),
        avg_win=float(avg_win), avg_loss=float(avg_loss),
        max_loss=float(max_loss), max_win=float(max_win),
        avg_credit=float(credits.mean()),
        capture_pct=float(cap_pct),
        blowups_gt_1k=blowups_1k, blowups_gt_5k=blowups_5k,
        years=yrs, pnl_per_year=float(total/yrs) if yrs>0 else 0,
        top5pct_abs_share=float(top5_share),
    )


def vix_bucket_analysis(trades, pnl_field="pnl_real"):
    if not trades: return {}
    df = pd.DataFrame([asdict(t) for t in trades])
    bins = [0, 12, 16, 20, 25, 35, 100]
    labels = ["<12", "12-16", "16-20", "20-25", "25-35", "35+"]
    df["vix_bin"] = pd.cut(df["vix"], bins=bins, labels=labels)
    g = df.groupby("vix_bin").agg(
        n=(pnl_field, "size"),
        total=(pnl_field, "sum"),
        avg=(pnl_field, "mean"),
        win_rate=(pnl_field, lambda x: (x > 0).mean() * 100),
        max_loss=(pnl_field, "min"),
    ).round(2)
    return g.to_dict()


def main():
    print("Loading data...")
    raw = load_all()
    spx = load_spx_daily()
    days = sorted(raw.keys())
    print(f"Period: {days[0]} -> {days[-1]}  ({len(days)} days)")

    summary = {}
    all_distribution = []  # for SPX/SPY drift compare

    # collect SPX overnight returns for baseline comparison
    overnight_returns = []
    for i, day in enumerate(days[:-1]):
        nxt = days[i+1]
        ts = pd.Timestamp(day); ts1 = pd.Timestamp(nxt)
        if ts not in spx.index or ts1 not in spx.index: continue
        c = float(spx.loc[ts, "spx_close"]); o = float(spx.loc[ts1, "spx_open"])
        overnight_returns.append((day.isoformat(), nxt.isoformat(), c, o, (o/c-1)*100))
    on_df = pd.DataFrame(overnight_returns, columns=["day_N", "day_N1", "spx_close_N", "spx_open_N1", "ret_pct"])
    on_df.to_csv(OUT_DIR / "spx_overnight_returns.csv", index=False)
    print(f"\nSPX overnight returns: n={len(on_df)}  mean={on_df['ret_pct'].mean():.4f}%  "
          f"std={on_df['ret_pct'].std():.3f}%  "
          f"gap>1%={int((on_df['ret_pct'].abs()>1).sum())} "
          f"gap>2%={int((on_df['ret_pct'].abs()>2).sum())}")
    # SPY buy-close-sell-open baseline (1 share equivalent, scale by $1000 notional)
    # P&L for $10k notional/night = ret_pct/100 * 10000
    avg_spy_pnl_10k = on_df['ret_pct'].mean() / 100 * 10000
    summary["__baseline_spx_buy_close_sell_open"] = dict(
        n=len(on_df),
        mean_overnight_pct=float(on_df['ret_pct'].mean()),
        std_overnight_pct=float(on_df['ret_pct'].std()),
        gap_gt_1pct=int((on_df['ret_pct'].abs()>1).sum()),
        gap_gt_2pct=int((on_df['ret_pct'].abs()>2).sum()),
        avg_pnl_per_night_10k_notional=float(avg_spy_pnl_10k),
        annualized_pnl_10k_notional=float(avg_spy_pnl_10k * 252),
    )

    # Single-leg
    OTM_GRID = [0.0025, 0.0050, 0.0100, 0.0150, 0.0200]
    DTE_GRID = [1, 2, 3]

    variants = []

    for side in ["P", "C"]:
        for dte in DTE_GRID:
            for otm in OTM_GRID:
                label = f"short_{'put' if side=='P' else 'call'}_DTE{dte}_OTM{int(otm*10000)}bp"
                print(f"\n--- {label} ---")
                trades = simulate_single_leg(raw, spx, dte, otm, side)
                if not trades:
                    print("  no trades")
                    continue
                s_real = stats(trades, "pnl_real")
                s_opt = stats(trades, "pnl_optimistic")
                s_no = stats(trades, "pnl_opt")
                summary[label] = dict(realistic=s_real, optimistic=s_opt, no_slip=s_no,
                                      vix_buckets_real=vix_bucket_analysis(trades))
                print(f"  n={s_real['n']}  wr={s_real['win_rate']:.1f}%  "
                      f"real_total=${s_real['total_pnl']:,.0f}  "
                      f"real_avg=${s_real['avg_pnl']:.1f}  "
                      f"sharpe={s_real['sharpe']:.2f}  maxL=${s_real['max_loss']:,.0f}  "
                      f"blowups>1k={s_real['blowups_gt_1k']}")
                df = pd.DataFrame([asdict(t) for t in trades])
                df.to_csv(OUT_DIR / f"trades_{label}.csv", index=False)
                variants.append((label, s_real, s_opt))

    # Straddles (otm=0)
    for dte in DTE_GRID:
        label = f"straddle_DTE{dte}_ATM"
        print(f"\n--- {label} ---")
        trades = simulate_straddle_or_strangle(raw, spx, dte, 0.0, "straddle")
        if not trades: print("  no trades"); continue
        s_real = stats(trades, "pnl_real")
        s_opt = stats(trades, "pnl_optimistic")
        s_no = stats(trades, "pnl_opt")
        summary[label] = dict(realistic=s_real, optimistic=s_opt, no_slip=s_no,
                              vix_buckets_real=vix_bucket_analysis(trades))
        print(f"  n={s_real['n']}  wr={s_real['win_rate']:.1f}%  real_total=${s_real['total_pnl']:,.0f}  "
              f"avg=${s_real['avg_pnl']:.1f}  sharpe={s_real['sharpe']:.2f}  maxL=${s_real['max_loss']:,.0f}")
        pd.DataFrame([asdict(t) for t in trades]).to_csv(OUT_DIR / f"trades_{label}.csv", index=False)
        variants.append((label, s_real, s_opt))

    # Strangles (0.5% OTM each side)
    for dte in DTE_GRID:
        label = f"strangle_DTE{dte}_OTM50bp"
        print(f"\n--- {label} ---")
        trades = simulate_straddle_or_strangle(raw, spx, dte, 0.005, "strangle")
        if not trades: print("  no trades"); continue
        s_real = stats(trades, "pnl_real")
        s_opt = stats(trades, "pnl_optimistic")
        s_no = stats(trades, "pnl_opt")
        summary[label] = dict(realistic=s_real, optimistic=s_opt, no_slip=s_no,
                              vix_buckets_real=vix_bucket_analysis(trades))
        print(f"  n={s_real['n']}  wr={s_real['win_rate']:.1f}%  real_total=${s_real['total_pnl']:,.0f}  "
              f"avg=${s_real['avg_pnl']:.1f}  sharpe={s_real['sharpe']:.2f}  maxL=${s_real['max_loss']:,.0f}")
        pd.DataFrame([asdict(t) for t in trades]).to_csv(OUT_DIR / f"trades_{label}.csv", index=False)
        variants.append((label, s_real, s_opt))

    # BPS overnight
    for dte in DTE_GRID:
        label = f"bps_overnight_DTE{dte}_OTM75bp_W50"
        print(f"\n--- {label} ---")
        trades = simulate_bps(raw, spx, dte, 0.0075, width=50)
        if not trades: print("  no trades"); continue
        s_real = stats(trades, "pnl_real")
        s_opt = stats(trades, "pnl_optimistic")
        s_no = stats(trades, "pnl_opt")
        summary[label] = dict(realistic=s_real, optimistic=s_opt, no_slip=s_no,
                              vix_buckets_real=vix_bucket_analysis(trades))
        print(f"  n={s_real['n']}  wr={s_real['win_rate']:.1f}%  real_total=${s_real['total_pnl']:,.0f}  "
              f"avg=${s_real['avg_pnl']:.1f}  sharpe={s_real['sharpe']:.2f}  maxL=${s_real['max_loss']:,.0f}")
        pd.DataFrame([asdict(t) for t in trades]).to_csv(OUT_DIR / f"trades_{label}.csv", index=False)
        variants.append((label, s_real, s_opt))

    with (OUT_DIR / "summary.json").open("w") as f:
        json.dump(summary, f, indent=2, default=str)

    # Build a leaderboard
    rows = []
    for label, s_real, s_opt in variants:
        rows.append(dict(
            variant=label,
            n=s_real["n"],
            win_rate=round(s_real["win_rate"], 1),
            real_total=round(s_real["total_pnl"], 0),
            real_avg=round(s_real["avg_pnl"], 2),
            real_sharpe=round(s_real["sharpe"], 3),
            real_max_loss=round(s_real["max_loss"], 0),
            real_blowup_1k=s_real["blowups_gt_1k"],
            real_blowup_5k=s_real["blowups_gt_5k"],
            opt_total=round(s_opt["total_pnl"], 0),
            opt_avg=round(s_opt["avg_pnl"], 2),
            opt_sharpe=round(s_opt["sharpe"], 3),
            top5pct_share=round(s_real["top5pct_abs_share"], 1),
            avg_credit=round(s_real["avg_credit"], 0),
            yrs=round(s_real["years"], 1),
            real_pnl_per_yr=round(s_real["pnl_per_year"], 0),
        ))
    lb = pd.DataFrame(rows).sort_values("real_sharpe", ascending=False)
    lb.to_csv(OUT_DIR / "leaderboard.csv", index=False)
    print("\n=== LEADERBOARD (by realistic sharpe) ===")
    print(lb.to_string(index=False))


if __name__ == "__main__":
    main()
