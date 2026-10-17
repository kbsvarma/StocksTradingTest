"""Pure strategy math — mirrors trend_replication_2026_07/engine2_cleanroom.py.

Everything here is stateless functions over a 60m bar DataFrame with columns
open/high/low/close (UTC DatetimeIndex, label = bar START). The bot's FSM
lives in bot.py; reconcile.py replays these same functions over the same bars.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

ET = "America/New_York"


def true_range(B: pd.DataFrame) -> pd.Series:
    pc = B["close"].shift(1)
    return pd.concat(
        [B["high"] - B["low"], (B["high"] - pc).abs(), (B["low"] - pc).abs()],
        axis=1,
    ).max(axis=1)


def atr(B: pd.DataFrame, n: int = 20) -> pd.Series:
    return true_range(B).rolling(n).mean()


def donchian(B: pd.DataFrame, n: int = 20) -> pd.Series:
    """Max of the PRIOR n closes (shifted 1 — no same-bar information)."""
    return B["close"].rolling(n).max().shift(1)


def vol_gate_threshold(B: pd.DataFrame, atr_s: pd.Series,
                       window: int = 8760, min_bars: int = 1440,
                       q: float = 0.95) -> float | None:
    """95th pct of ATR% over the trailing window, evaluated at the last bar."""
    ap = (atr_s / B["close"]).dropna()
    if len(ap) < min_bars:
        return None
    return float(ap.tail(window).quantile(q))


def signal_frame(B: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """Return the production signal inputs and verdict for every bar.

    Research must call this function too.  Keeping the rolling threshold,
    session gate, and shifted Donchian calculation here prevents a backtest
    from silently drifting away from the live last-bar decision path.
    """
    a = atr(B, cfg["atr_n"])
    d = donchian(B, cfg["donchian_n"])
    ap = a / B["close"]
    threshold = ap.rolling(
        cfg["vol_gate_window_bars"],
        min_periods=cfg["vol_gate_min_bars"],
    ).quantile(cfg["vol_gate_q"])
    hours = B.index.tz_convert(ET).hour
    lo, hi = cfg["session_block_et"]
    breakout = B["close"] > d
    vol_ok = ~(ap > threshold) & threshold.notna()
    session_ok = ~((hours >= lo) & (hours < hi))
    out = pd.DataFrame(index=B.index)
    out["atr"] = a
    out["donchian"] = d
    out["atr_pct"] = ap
    out["vol_thresh"] = threshold
    out["breakout"] = breakout.fillna(False).astype(bool)
    out["vol_ok"] = vol_ok.fillna(False).astype(bool)
    out["session_ok"] = np.asarray(session_ok, dtype=bool)
    out["signal"] = (
        out["breakout"] & out["vol_ok"] & out["session_ok"]
        & out["atr"].notna()
    )
    return out


def signal_on_last_bar(B: pd.DataFrame, cfg: dict) -> dict:
    """Evaluate the entry signal at the close of the LAST bar in B.

    Returns a dict with the signal verdict and each gate's state (for the
    dashboard and the decision log)."""
    n = cfg["donchian_n"]
    if len(B) < max(n + 2, cfg["atr_n"] + 2):
        return {"signal": False, "reason": "warmup"}

    frame = signal_frame(B, cfg)
    last = B.index[-1]
    close = float(B["close"].iloc[-1])
    row = frame.iloc[-1]
    atr_last = float(row["atr"])
    donch_last = float(row["donchian"])
    thresh_value = float(row["vol_thresh"])
    thresh = None if math.isnan(thresh_value) else thresh_value
    atr_pct = float(row["atr_pct"])
    et_hour = last.tz_convert(ET).hour

    return {
        "signal": bool(row["signal"]),
        "bar": last.isoformat(),
        "close": close,
        "donchian": None if math.isnan(donch_last) else donch_last,
        "atr": None if math.isnan(atr_last) else atr_last,
        "atr_pct": None if math.isnan(atr_pct) else atr_pct,
        "vol_thresh": thresh,
        "breakout": bool(row["breakout"]),
        "vol_ok": bool(row["vol_ok"]),
        "session_ok": bool(row["session_ok"]),
        "et_hour": int(et_hour),
    }


def trail_stop(hw: float, atr_last: float, k: float, cur_stop: float) -> float:
    """Chandelier trail: stop never moves down."""
    return max(cur_stop, hw - k * atr_last)


def initial_stop(entry_px: float, atr_signal: float, k: float) -> float:
    return entry_px - k * atr_signal


def floor_to_tick(px: float, tick: float) -> float:
    """Round DOWN to tick — a sell stop may sit ≤1 tick wider than the model,
    never tighter."""
    return math.floor(px / tick + 1e-9) * tick
