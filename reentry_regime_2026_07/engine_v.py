"""Variant FSM for the re-entry/regime program.

`simulate_v(..., h1=None, h2=None)` with both None must reproduce
rotation_grid.simulate_weighted EXACTLY (gate zero). H1/H2 hooks:

h1 = dict(kind="A"|"B"|"C", k_re=int, m=float, n=int)   fast re-entry after
     stop exits: A close>max(prior k_re closes), B close>exit_stop+m*ATR,
     C = A(k_re) or B(m); active while bars_since_stop < n.
h2 = dict(det="ER"|"ADX"|"WIDTH"|"WHIP", mode="M1".."M4",
          pctl=0.30, h1sub=dict(kind="A", k_re=10, n=80))
     regime binds at entry; M1 block entries in chop, M2 unlock h1sub only
     when not chop, M3 k_trail 1.0 for chop-entered positions, M4 profit
     target entry+1.0*ATR_sig for chop-entered positions (stop checked
     FIRST — pessimistic).
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

TB = Path(__file__).resolve().parent.parent / "trend_bot"
sys.path.insert(0, str(TB))
sys.path.insert(0, str(TB / "research"))

import rotation_grid as rg              # noqa: E402
import strategy                         # noqa: E402


def wilder_adx(B: pd.DataFrame, n: int = 14) -> pd.Series:
    up = B["high"].diff()
    dn = -B["low"].diff()
    plus_dm = np.where((up > dn) & (up > 0), up, 0.0)
    minus_dm = np.where((dn > up) & (dn > 0), dn, 0.0)
    tr = strategy.true_range(B)
    alpha = 1.0 / n
    atr_w = tr.ewm(alpha=alpha, adjust=False).mean()
    pdi = 100 * pd.Series(plus_dm, index=B.index).ewm(
        alpha=alpha, adjust=False).mean() / atr_w
    mdi = 100 * pd.Series(minus_dm, index=B.index).ewm(
        alpha=alpha, adjust=False).mean() / atr_w
    dx = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)
    return dx.ewm(alpha=alpha, adjust=False).mean()


def precompute(B: pd.DataFrame, scfg: dict) -> dict:
    """Shared per-symbol features for every cell (computed once)."""
    signals = strategy.signal_frame(B, scfg)
    c = B["close"]
    feats = {"signals": signals,
             "re_hi": {k: c.rolling(k).max().shift(1).values
                       for k in (5, 10, 15)}}
    er = ((c - c.shift(20)).abs()
          / c.diff().abs().rolling(20).sum()).replace([np.inf], np.nan)
    adx = wilder_adx(B, 14)
    width = ((c.rolling(20).max() - c.rolling(20).min())
             / signals["atr"]).replace([np.inf], np.nan)
    for name, s in (("ER", er), ("ADX", adx), ("WIDTH", width)):
        for pc in (0.25, 0.30, 0.35):
            thr = s.rolling(1000, min_periods=200).quantile(pc)
            feats[f"chop_{name}_{pc:.2f}"] = (s < thr).fillna(False).values
    return feats


def simulate_v(B: pd.DataFrame, sym: str, week_qty, feats: dict,
               h1: dict | None = None, h2: dict | None = None,
               scfg: dict | None = None) -> pd.DataFrame:
    scfg = scfg or rg.SCFG
    signals = feats["signals"]
    atr_values = signals["atr"].values
    sig = signals["signal"].values
    vol_ok = signals["vol_ok"].values
    sess_ok = signals["session_ok"].values
    atr_ok = signals["atr"].notna().values
    opens, highs, lows, closes = (
        B[c].values for c in ("open", "high", "low", "close"))
    rolls = B["roll"].values
    ts = B.index

    h2_det = h2["det"] if h2 else None
    chop = (feats[f"chop_{h2_det}_{h2.get('pctl', 0.30):.2f}"]
            if h2 and h2_det != "WHIP" else None)
    whip_exits: list[int] = []          # bar indices of stop exits (D4)

    def is_chop(i: int) -> bool:
        if h2 is None:
            return False
        if h2_det == "WHIP":
            return sum(1 for j in whip_exits if i - j <= 80) >= 2
        return bool(chop[i])

    def h1_rule(i: int, rule: dict, stop_px: float) -> bool:
        kind = rule["kind"]
        ok_a = (closes[i] > feats["re_hi"][rule.get("k_re", 10)][i]
                if kind in ("A", "C") else False)
        ok_b = (closes[i] > stop_px + rule.get("m", 0.5) * atr_values[i]
                if kind in ("B", "C") and np.isfinite(stop_px)
                and atr_ok[i] else False)
        return bool((ok_a or ok_b) and vol_ok[i] and sess_ok[i] and atr_ok[i])

    trades = []
    pos = pend = 0
    e = hw = st = sa = np.nan
    qty = 0
    e_ts = None
    pend_chop = pos_chop = False
    last_stop_bar = -10**9
    last_stop_px = np.nan
    for i in range(len(B)):
        if pend and rolls[i] == 1:
            pend = 0
        if pend:
            pos, pend = 1, 0
            e, hw, sa = opens[i], opens[i], atr_values[i - 1]
            st = e - scfg["k_trail"] * sa
            pos_chop = pend_chop
            e_ts = ts[i]
            if np.isnan(st):
                pos = 0
        if pos and rolls[i] == 1:
            x = closes[i - 1] if i > 0 else opens[i]
            trades.append((e_ts, ts[i], sym, e, x, x - e, "roll", qty))
            pos = 0
        if pos:
            k_eff = (1.0 if (h2 and h2["mode"] == "M3" and pos_chop)
                     else scfg["k_trail"])
            if lows[i] <= st:
                x = min(st, opens[i])
                trades.append((e_ts, ts[i], sym, e, x, x - e, "stop", qty))
                pos = 0
                last_stop_bar, last_stop_px = i, st
                whip_exits.append(i)
            elif (h2 and h2["mode"] == "M4" and pos_chop
                  and highs[i] >= e + 1.0 * sa):
                x = max(e + 1.0 * sa, opens[i])
                trades.append((e_ts, ts[i], sym, e, x, x - e, "pt", qty))
                pos = 0
            else:
                hw = max(hw, highs[i])
                st = max(st, hw - k_eff * atr_values[i])
        if pos == 0 and pend == 0 and i > scfg["donchian_n"]:
            fire = bool(sig[i])
            if not fire:
                rule = None
                if h1 is not None and i - last_stop_bar < h1["n"]:
                    rule = h1
                elif (h2 and h2["mode"] == "M2"
                      and i - last_stop_bar < h2["h1sub"]["n"]
                      and not is_chop(i)):
                    rule = h2["h1sub"]
                if rule is not None:
                    fire = h1_rule(i, rule, last_stop_px)
            if fire and h2 and h2["mode"] == "M1" and is_chop(i):
                fire = False
            if fire:
                q = week_qty(ts[i])
                if q > 0:
                    pend, qty = 1, q
                    pend_chop = is_chop(i)
    T = pd.DataFrame(trades, columns=["entry_time", "exit_time", "sym",
                                      "entry_px", "exit_px", "points",
                                      "reason", "qty"])
    return T[T["entry_time"] >= rg.START].reset_index(drop=True)
