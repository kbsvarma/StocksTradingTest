"""
Broad Strategy Sweep — Multiple Underlyings, Multiple Approaches
================================================================
Covers strategies and underlyings not yet explored.

Categories
----------
A. VIX-based strategies          (VIX mean reversion, term structure)
B. QQQ/IWM momentum & reversion  (Nasdaq & Small-cap)
C. Overnight ES drift             (long ES overnight, flat RTH)
D. Earnings vol crush             (proxy via UVXY/VIX behavior)
E. TLT / Bond strategies          (rate trend following)
F. GLD / Gold strategies          (safe haven / inflation hedge)
G. VIX term structure             (VIX vs VIX3M contango/backwardation)
H. Cross-asset momentum           (sector rotation proxy)
I. Mean reversion after gap       (all underlyings, not just SPX)
J. Seasonality                    (calendar-based patterns)

$100 per unit sizing throughout for comparability.
BPS reference: $183,312 / 453 trades / Sharpe 4.59
"""

from __future__ import annotations
from pathlib import Path
import numpy as np
import pandas as pd

MD = Path(__file__).parent / "ten_year" / "market_data"
PPU = 100  # dollars per unit/point for all strategies

BPS = dict(label="── BPS baseline ──", n=453, total=183312,
           win=0.755, avg=405, dd=-9756, sharpe=4.594)


def load(name: str) -> pd.DataFrame:
    df = pd.read_csv(MD / f"{name.lower()}.csv", index_col="date", parse_dates=True)
    return df


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    c = df["close"]
    delta = c.diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()
    df["rsi"] = 100 - 100 / (1 + gain / loss.replace(0, np.nan))
    df["ma20"]  = c.rolling(20).mean()
    df["ma50"]  = c.rolling(50).mean()
    df["ma200"] = c.rolling(200).mean()
    df["atr14"] = pd.concat([
        df["high"] - df["low"],
        (df["high"] - c.shift()).abs(),
        (df["low"] - c.shift()).abs()
    ], axis=1).max(axis=1).rolling(14).mean()
    df["prev_close"] = c.shift(1)
    df["gap_pct"] = (df["open"] - df["prev_close"]) / df["prev_close"]
    df["day_ret"] = c - df["open"]
    df["pct_chg"] = c.pct_change()
    df["vol20"] = c.pct_change().rolling(20).std()
    return df.dropna(subset=["ma200"])


def metrics(trades, label: str = "") -> dict:
    if isinstance(trades, pd.Series):
        p = trades.values
    elif isinstance(trades, list):
        if not trades:
            p = np.array([])
        elif isinstance(trades[0], dict):
            p = np.array([t["pnl"] for t in trades])
        else:
            p = np.array(trades)
    else:
        p = np.array(trades)
    if len(p) == 0:
        return dict(label=label, n=0, total=0, win=0, avg=0, dd=0, sharpe=0)
    cum = np.cumsum(p)
    dd  = float((cum - np.maximum.accumulate(cum)).min())
    avg = float(p.mean())
    std = float(p.std(ddof=1)) if len(p) > 1 else 1.0
    return dict(label=label, n=len(p), total=round(float(p.sum()), 0),
                win=round(float((p > 0).mean()), 3), avg=round(avg, 1),
                dd=round(dd, 0), sharpe=round((avg / std) * np.sqrt(252) if std > 0 else 0, 3))


def ptable(rows: list[dict], title: str = "") -> None:
    if title:
        print(f"\n{'═'*90}")
        print(f"  {title}")
        print(f"{'═'*90}")
    hdr = f"{'Strategy':<44} {'N':>5} {'Total$':>10} {'Win%':>6} {'Avg$':>7} {'MaxDD':>10} {'Sharpe':>7}"
    print(hdr); print("-" * len(hdr))
    for r in rows:
        marker = " ★" if r.get("sharpe", 0) > BPS["sharpe"] else ""
        print(f"{r['label']:<44} {r['n']:>5} ${r['total']:>9,.0f} "
              f"{r['win']*100:>5.1f}% ${r['avg']:>6,.0f} ${r['dd']:>9,.0f} "
              f"{r['sharpe']:>7.3f}{marker}")


# ═══════════════════════════════════════════════════════
# A. VIX MEAN REVERSION
# ═══════════════════════════════════════════════════════
def vix_spike_buy_spy(spx, vix):
    """When VIX spikes >20% in a day, buy SPX next open, hold 5 days."""
    trades = []
    vix_chg = vix["close"].pct_change()
    dates = spx.index
    i = 0
    while i < len(dates) - 6:
        if vix_chg.iloc[i] > 0.20:
            entry = spx["open"].iloc[i + 1]
            exit_ = spx["close"].iloc[i + 5]
            pnl = (exit_ - entry) / spx["close"].iloc[i] * PPU * 50
            trades.append({"pnl": pnl})
            i += 6
        else:
            i += 1
    return trades


def vix_extreme_long(spx, vix, threshold=30):
    """Long SPX when VIX > threshold. Exit when VIX < 22."""
    trades = []
    in_trade = False
    entry = entry_vix = None
    for i in range(1, len(spx)):
        v = vix["close"].iloc[i]
        if not in_trade and v > threshold:
            entry = spx["open"].iloc[i]
            in_trade = True
        elif in_trade and v < 22:
            pnl = (spx["close"].iloc[i] - entry) / entry * PPU * 50
            trades.append({"pnl": pnl})
            in_trade = False
    return trades


# ═══════════════════════════════════════════════════════
# B. VIX TERM STRUCTURE (contango decay)
# ═══════════════════════════════════════════════════════
def vix_term_structure(vix, vix3m):
    """Short UVXY-proxy when VIX3M > VIX * 1.05 (contango).
       Proxy: each day in contango VIX tends to fall → short vol pays.
       We model: short 1 unit of vol per day in contango, collect decay.
       Simplified: daily pnl = (VIX_t-1 - VIX_t) * 50 when in contango."""
    pnls = []
    for i in range(1, len(vix)):
        v  = vix["close"].iloc[i]
        v3 = vix3m["close"].iloc[i]
        pv = vix["close"].iloc[i - 1]
        if v3 > v * 1.05:  # contango: futures more expensive than spot
            decay = (pv - v) * PPU  # VIX falling = short vol profits
            pnls.append(decay)
    return pnls


# ═══════════════════════════════════════════════════════
# C. OVERNIGHT ES DRIFT
# ═══════════════════════════════════════════════════════
def overnight_drift(spx):
    """Long SPX overnight: enter at close, exit at next open.
       Well-documented: most of SPX long-term return comes overnight."""
    pnls = []
    for i in range(len(spx) - 1):
        close_px = spx["close"].iloc[i]
        next_open = spx["open"].iloc[i + 1]
        pnl = (next_open - close_px) / close_px * PPU * 50
        pnls.append(pnl)
    return pnls


def intraday_only(spx):
    """Long at open, exit at close (opposite of overnight)."""
    pnls = []
    for i in range(len(spx)):
        pnl = (spx["close"].iloc[i] - spx["open"].iloc[i]) / spx["open"].iloc[i] * PPU * 50
        pnls.append(pnl)
    return pnls


# ═══════════════════════════════════════════════════════
# D. QQQ STRATEGIES
# ═══════════════════════════════════════════════════════
def rsi_mean_rev(df, ticker, rsi_lo=30, rsi_hi=50, hold=15):
    trades = []
    in_t = False; entry = entry_i = None
    for i in range(len(df)):
        r = df["rsi"].iloc[i]
        if not in_t and r < rsi_lo:
            entry = df["close"].iloc[i]; entry_i = i; in_t = True
        elif in_t:
            if df["rsi"].iloc[i] > rsi_hi or (i - entry_i) >= hold:
                pnl = (df["close"].iloc[i] - entry) / entry * PPU * 20
                trades.append({"pnl": pnl}); in_t = False
    return trades


def ma_pullback(df, ticker):
    """Buy pullbacks to 50MA in an uptrend (price > 200MA). Exit at 20MA cross."""
    trades = []
    in_t = False; entry = None
    for i in range(1, len(df)):
        c = df["close"].iloc[i]
        m50  = df["ma50"].iloc[i]
        m200 = df["ma200"].iloc[i]
        m20  = df["ma20"].iloc[i]
        prev_c = df["close"].iloc[i-1]
        if not in_t and c > m200 and prev_c < m50 and c > m50:
            entry = c; in_t = True
        elif in_t and c > m20:
            pnl = (c - entry) / entry * PPU * 20
            trades.append({"pnl": pnl}); in_t = False
    return trades


def qqq_spy_divergence(qqq, spx):
    """When QQQ diverges from SPX (QQQ/SPX ratio at 20-day low), long QQQ."""
    ratio = qqq["close"] / spx["close"]
    ratio_low = ratio.rolling(20).min()
    trades = []
    in_t = False; entry = None
    for i in range(20, len(qqq)):
        if not in_t and ratio.iloc[i] <= ratio_low.iloc[i]:
            entry = qqq["close"].iloc[i]; in_t = True
        elif in_t and ratio.iloc[i] > ratio.rolling(20).mean().iloc[i]:
            pnl = (qqq["close"].iloc[i] - entry) / entry * PPU * 20
            trades.append({"pnl": pnl}); in_t = False
    return trades


# ═══════════════════════════════════════════════════════
# E. IWM (SMALL CAP) STRATEGIES
# ═══════════════════════════════════════════════════════
def iwm_spy_rotation(iwm, spx):
    """Rotate into IWM when it's outperforming SPX on 20-day basis."""
    iwm_ret = iwm["close"].pct_change(20)
    spx_ret = spx["close"].pct_change(20)
    trades = []
    in_t = False; entry = None
    for i in range(20, len(iwm)):
        outperform = iwm_ret.iloc[i] > spx_ret.iloc[i]
        if not in_t and outperform:
            entry = iwm["close"].iloc[i]; in_t = True
        elif in_t and not outperform:
            pnl = (iwm["close"].iloc[i] - entry) / entry * PPU * 15
            trades.append({"pnl": pnl}); in_t = False
    return trades


def small_cap_reversion(iwm):
    """IWM RSI < 35 long, exit at RSI > 55."""
    return rsi_mean_rev(iwm, "IWM", rsi_lo=35, rsi_hi=55, hold=10)


# ═══════════════════════════════════════════════════════
# F. GLD (GOLD) STRATEGIES
# ═══════════════════════════════════════════════════════
def gold_trend(gld):
    """Long GLD when above 200MA. Short when below. Exit on cross."""
    trades = []
    position = None; entry = None
    for i in range(1, len(gld)):
        c    = gld["close"].iloc[i]
        m200 = gld["ma200"].iloc[i]
        prev_c = gld["close"].iloc[i-1]
        prev_m = gld["ma200"].iloc[i-1]
        cross_up   = prev_c < prev_m and c > m200
        cross_down = prev_c > prev_m and c < m200
        if position == "long" and cross_down:
            pnl = (c - entry) / entry * PPU * 10
            trades.append({"pnl": pnl}); position = None
        elif position == "short" and cross_up:
            pnl = (entry - c) / entry * PPU * 10
            trades.append({"pnl": pnl}); position = None
        if position is None:
            if cross_up:   position = "long";  entry = c
            elif cross_down: position = "short"; entry = c
    return trades


def gold_safe_haven(gld, spx):
    """Buy GLD when SPX drops >2% in a week. Exit after 10 days."""
    trades = []
    spx_5d = spx["close"].pct_change(5)
    in_t = False; entry = entry_i = None
    for i in range(5, len(gld)):
        if not in_t and spx_5d.iloc[i] < -0.02:
            entry = gld["close"].iloc[i]; entry_i = i; in_t = True
        elif in_t and (i - entry_i) >= 10:
            pnl = (gld["close"].iloc[i] - entry) / entry * PPU * 10
            trades.append({"pnl": pnl}); in_t = False
    return trades


# ═══════════════════════════════════════════════════════
# G. TLT (BONDS) STRATEGIES
# ═══════════════════════════════════════════════════════
def bond_equity_rotation(tlt, spx):
    """When SPX 20-day return < 0, rotate to TLT. Else hold SPX."""
    spx_20 = spx["close"].pct_change(20)
    in_bond = False; entry = None
    trades = []
    for i in range(20, len(tlt)):
        if not in_bond and spx_20.iloc[i] < 0:
            entry = tlt["close"].iloc[i]; in_bond = True
        elif in_bond and spx_20.iloc[i] > 0:
            pnl = (tlt["close"].iloc[i] - entry) / entry * PPU * 10
            trades.append({"pnl": pnl}); in_bond = False
    return trades


def bond_rate_momentum(tlt):
    """Long TLT when RSI < 35 (rates spiked, bonds oversold). Exit RSI > 50."""
    return rsi_mean_rev(tlt, "TLT", rsi_lo=35, rsi_hi=50, hold=20)


# ═══════════════════════════════════════════════════════
# H. SEASONALITY
# ═══════════════════════════════════════════════════════
def seasonality_spx(spx):
    """Sell in May, Buy in November (classic seasonal pattern).
       Long Oct 31 → Apr 30. Flat May–Sep."""
    trades = []
    in_t = False; entry = None
    for i in range(len(spx)):
        m = spx.index[i].month
        d = spx.index[i].day
        c = spx["close"].iloc[i]
        if not in_t and m == 10 and d >= 28:
            entry = c; in_t = True
        elif in_t and m == 4 and d >= 28:
            pnl = (c - entry) / entry * PPU * 50
            trades.append({"pnl": pnl}); in_t = False
    return trades


def monthly_options_expiry(spx):
    """SPX tends to drift up in first week of month (401k inflows).
       Long first 5 trading days of each month."""
    trades = []
    prev_month = -1
    trade_start = None; day_count = 0; entry = None
    for i in range(len(spx)):
        m = spx.index[i].month
        c = spx["close"].iloc[i]
        if m != prev_month:
            prev_month = m; day_count = 0; entry = spx["open"].iloc[i]
        day_count += 1
        if day_count == 5:
            pnl = (c - entry) / entry * PPU * 50
            trades.append({"pnl": pnl})
    return trades


# ═══════════════════════════════════════════════════════
# I. CROSS-ASSET RELATIVE VALUE
# ═══════════════════════════════════════════════════════
def spy_tlt_pairs(spx, tlt):
    """When TLT drops > SPX (flight from bonds into stocks), go long SPX.
       When TLT rises while SPX flat, rotate to TLT."""
    tlt_5 = tlt["close"].pct_change(5)
    spx_5 = spx["close"].pct_change(5)
    trades = []
    in_t = False; entry = None
    for i in range(5, len(spx)):
        outflow_bonds = tlt_5.iloc[i] < -0.02 and spx_5.iloc[i] > tlt_5.iloc[i]
        if not in_t and outflow_bonds:
            entry = spx["close"].iloc[i]; in_t = True
        elif in_t and tlt_5.iloc[i] > spx_5.iloc[i]:
            pnl = (spx["close"].iloc[i] - entry) / entry * PPU * 50
            trades.append({"pnl": pnl}); in_t = False
    return trades


def vix_gld_combo(vix, gld, spx):
    """When VIX > 25 AND GLD is rising (5-day > 0): buy SPX (fear oversold + gold hedge active).
       Exit when VIX < 20."""
    gld_5 = gld["close"].pct_change(5)
    trades = []
    in_t = False; entry = None
    for i in range(5, len(spx)):
        v = vix["close"].iloc[i]
        g = gld_5.iloc[i]
        if not in_t and v > 25 and g > 0:
            entry = spx["close"].iloc[i]; in_t = True
        elif in_t and v < 20:
            pnl = (spx["close"].iloc[i] - entry) / entry * PPU * 50
            trades.append({"pnl": pnl}); in_t = False
    return trades


# ═══════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════
def main():
    print("Loading data...", flush=True)
    spx_raw = pd.read_csv(MD / "spx_vix_daily.csv", index_col="date", parse_dates=True)
    spx  = add_indicators(spx_raw[["spx_open","spx_high","spx_low","spx_close"]].rename(
        columns={"spx_open":"open","spx_high":"high","spx_low":"low","spx_close":"close"}))
    vix  = add_indicators(load("vix"))
    vix3m = load("vix3m")
    qqq  = add_indicators(load("qqq"))
    iwm  = add_indicators(load("iwm"))
    gld  = add_indicators(load("gld"))
    tlt  = add_indicators(load("tlt"))

    # align all to common index
    idx = spx.index
    vix   = vix.reindex(idx).ffill()
    vix3m = vix3m.reindex(idx).ffill()
    qqq   = qqq.reindex(idx).ffill()
    iwm   = iwm.reindex(idx).ffill()
    gld   = gld.reindex(idx).ffill()
    tlt   = tlt.reindex(idx).ffill()

    all_rows = [BPS]

    cats = {
        "A. VIX Strategies": [
            ("VIX spike >20% → long SPX 5d",     vix_spike_buy_spy(spx, vix)),
            ("VIX > 30 long SPX → exit VIX<22",  vix_extreme_long(spx, vix, 30)),
            ("VIX term structure contango decay", vix_term_structure(vix, vix3m)),
        ],
        "B. Overnight vs Intraday": [
            ("Overnight drift (close→next open)",  overnight_drift(spx)),
            ("Intraday only (open→close)",         intraday_only(spx)),
        ],
        "C. QQQ Strategies": [
            ("QQQ RSI<30 mean reversion",          rsi_mean_rev(qqq, "QQQ")),
            ("QQQ 50MA pullback in uptrend",       ma_pullback(qqq, "QQQ")),
            ("QQQ/SPX divergence long QQQ",        qqq_spy_divergence(qqq, spx)),
        ],
        "D. IWM (Small Cap)": [
            ("IWM outperformance rotation",        iwm_spy_rotation(iwm, spx)),
            ("IWM RSI<35 mean reversion",          small_cap_reversion(iwm)),
        ],
        "E. GLD (Gold)": [
            ("Gold trend (200MA cross)",           gold_trend(gld)),
            ("Gold safe haven (SPX -2% in 5d)",    gold_safe_haven(gld, spx)),
        ],
        "F. TLT (Bonds)": [
            ("Bond/equity rotation",               bond_equity_rotation(tlt, spx)),
            ("TLT RSI<35 mean reversion",          bond_rate_momentum(tlt)),
        ],
        "G. Seasonality": [
            ("Sell in May / Buy in Nov (SPX)",     seasonality_spx(spx)),
            ("Month-start drift (first 5 days)",   monthly_options_expiry(spx)),
        ],
        "H. Cross-Asset": [
            ("SPY/TLT pairs rotation",             spy_tlt_pairs(spx, tlt)),
            ("VIX>25 + GLD rising → long SPX",     vix_gld_combo(vix, gld, spx)),
        ],
    }

    for cat_title, strats in cats.items():
        rows = [BPS]
        for label, trades in strats:
            if isinstance(trades, list) and trades and isinstance(trades[0], dict):
                pnls = [t["pnl"] for t in trades]
            else:
                pnls = trades
            rows.append(metrics(pnls, label))
        ptable(rows, cat_title)
        all_rows.extend(rows[1:])

    # master ranking
    all_rows.sort(key=lambda r: r.get("sharpe", 0), reverse=True)
    ptable(all_rows[:15], "TOP 15 ACROSS ALL STRATEGIES (★ beats BPS Sharpe 4.59)")


if __name__ == "__main__":
    main()
