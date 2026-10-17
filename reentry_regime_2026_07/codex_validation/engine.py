"""Roll-aware validation engine for the H1/H2 study.

With ``h1=None`` and ``h2=None`` this is required to reproduce
``rotation_grid.simulate_weighted`` exactly.  Research only: no live imports,
state writes, broker calls, or configuration changes.
"""
from __future__ import annotations

from collections import deque
from pathlib import Path
import sys

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "trend_bot"),
                str(ROOT / "trend_bot" / "research")]

import rotation_grid as rg  # noqa: E402
import strategy  # noqa: E402


STATIC_DETECTORS = ("ER20", "ADX14", "WIDTH20")


def wilder_adx(bars: pd.DataFrame, n: int = 14) -> pd.Series:
    """Causal Wilder-style ADX from completed OHLC bars."""
    up = bars["high"].diff()
    down = -bars["low"].diff()
    plus_dm = pd.Series(
        np.where((up > down) & (up > 0), up, 0.0), index=bars.index)
    minus_dm = pd.Series(
        np.where((down > up) & (down > 0), down, 0.0), index=bars.index)
    tr = strategy.true_range(bars)
    alpha = 1.0 / n
    atr = tr.ewm(alpha=alpha, adjust=False).mean()
    plus_di = 100.0 * plus_dm.ewm(alpha=alpha, adjust=False).mean() / atr
    minus_di = 100.0 * minus_dm.ewm(alpha=alpha, adjust=False).mean() / atr
    denom = (plus_di + minus_di).replace(0.0, np.nan)
    dx = 100.0 * (plus_di - minus_di).abs() / denom
    return dx.ewm(alpha=alpha, adjust=False).mean()


def precompute(bars: pd.DataFrame, scfg: dict | None = None,
               percentile: float = 0.30, *, width_n: int = 20,
               regime_window: int = 1000, regime_min: int = 200) -> dict:
    """Compute features once; percentile thresholds use prior bars only."""
    scfg = scfg or rg.SCFG
    signals = strategy.signal_frame(bars, scfg)
    close = bars["close"]
    re_high = {
        k: close.rolling(k).max().shift(1).to_numpy(dtype=float)
        for k in (5, 10, 15)
    }
    er20 = ((close - close.shift(20)).abs()
            / close.diff().abs().rolling(20).sum()).replace(
                [np.inf, -np.inf], np.nan)
    adx14 = wilder_adx(bars, 14)
    width20 = ((close.rolling(width_n).max() - close.rolling(width_n).min())
               / signals["atr"]).replace([np.inf, -np.inf], np.nan)
    raw = {"ER20": er20, "ADX14": adx14, "WIDTH20": width20}
    chop = {}
    for name, value in raw.items():
        # shift(1) makes the comparison distribution strictly prior to the
        # current completed observation.
        threshold = value.rolling(regime_window, min_periods=regime_min).quantile(
            percentile).shift(1)
        chop[name] = (value < threshold).fillna(False).to_numpy(dtype=bool)
    return {
        "signals": signals,
        "re_high": re_high,
        "raw_regime": raw,
        "chop": chop,
        "percentile": percentile,
        "width_n": width_n,
        "regime_window": regime_window,
    }


def _fast_rule(i: int, rule: dict, closes: np.ndarray,
               atr: np.ndarray, feats: dict, anchor: dict) -> bool:
    """Return a causal post-stop signal at completed bar i."""
    stop_i = anchor["bar"]
    elapsed = i - stop_i
    min_elapsed = int(rule.get("min_elapsed", 1))
    if elapsed < min_elapsed or elapsed > int(rule["horizon"]):
        return False
    kind = rule["kind"]
    if kind == "SC":
        level = feats["re_high"][int(rule["k"])][i]
        return bool(np.isfinite(level) and closes[i] > level)
    if kind == "RESET":
        # The stop bar and its potentially pre-stop high are excluded. At
        # least two completed post-stop closes must establish the new anchor.
        prior = closes[stop_i + 1:i]
        prior = prior[np.isfinite(prior)]
        return bool(len(prior) >= 2 and closes[i] > prior.max())
    if kind == "ATR":
        level = anchor["fill"] + float(rule["m"]) * anchor["atr"]
        return bool(np.isfinite(level) and closes[i] > level)
    if kind == "FRAC":
        level = (anchor["fill"] + float(rule["r"])
                 * (anchor["high_water"] - anchor["fill"]))
        return bool(np.isfinite(level) and closes[i] > level)
    raise ValueError(f"unknown H1 kind: {kind}")


def simulate(bars: pd.DataFrame, sym: str, week_qty, feats: dict,
             *, h1: dict | None = None, h2: dict | None = None,
             scfg: dict | None = None) -> pd.DataFrame:
    """Simulate one symbol with optional H1/H2 state-dependent behavior."""
    scfg = scfg or rg.SCFG
    frame = feats["signals"]
    atr = frame["atr"].to_numpy(dtype=float)
    normal_signal = frame["signal"].to_numpy(dtype=bool)
    gate = (frame["vol_ok"].to_numpy(dtype=bool)
            & frame["session_ok"].to_numpy(dtype=bool)
            & frame["atr"].notna().to_numpy(dtype=bool))
    opens, highs, lows, closes = (
        bars[c].to_numpy(dtype=float) for c in ("open", "high", "low", "close"))
    rolls = bars["roll"].to_numpy(dtype=int)
    stamps = bars.index

    detector = h2.get("detector") if h2 else None
    mode = h2.get("mode") if h2 else None
    static_chop = (None if detector == "WHIP80" or not h2
                   else feats["chop"][detector])
    stop_bars: deque[int] = deque()

    def is_chop(i: int) -> bool:
        if not h2:
            return False
        if detector == "WHIP80":
            while stop_bars and i - stop_bars[0] > 80:
                stop_bars.popleft()
            return len(stop_bars) >= 2
        return bool(static_chop[i])

    rows = []
    pos = False
    pending = False
    pending_qty = 0
    pending_kind = ""
    pending_chop = False
    pending_time_exit = False
    entry = high_water = stop = signal_atr = np.nan
    entry_time = None
    entry_kind = ""
    entry_chop = False
    qty = 0
    holding_bars = 0
    post_stop: dict | None = None

    def record_exit(i: int, px: float, reason: str) -> None:
        nonlocal pos, pending_time_exit
        rows.append((entry_time, stamps[i], sym, entry, px, px - entry,
                     reason, 1, qty, entry_kind, entry_chop))
        pos = False
        pending_time_exit = False

    for i in range(len(bars)):
        # A pending entry cannot cross a contract splice.
        if pending and rolls[i]:
            pending = False
            pending_qty = 0
        if pending:
            pos, pending = True, False
            qty = pending_qty
            entry_kind = pending_kind
            entry_chop = pending_chop
            entry = high_water = opens[i]
            signal_atr = atr[i - 1]
            k_entry = (1.0 if mode == "TIGHT" and entry_chop
                       else float(scfg["k_trail"]))
            stop = entry - k_entry * signal_atr
            entry_time = stamps[i]
            holding_bars = 0
            post_stop = None                 # re-entry opportunity consumed
            if not np.isfinite(stop):
                pos = False

        # A six-bar close-confirmed time exit is filled at the following open.
        # If that open is a splice, use the prior contract close instead.
        if pos and pending_time_exit:
            if rolls[i]:
                record_exit(i, closes[i - 1] if i else opens[i], "roll")
            else:
                record_exit(i, opens[i], "time")

        if pos and rolls[i]:
            record_exit(i, closes[i - 1] if i else opens[i], "roll")
            post_stop = None

        if pos:
            k_effective = (1.0 if mode == "TIGHT" and entry_chop
                           else float(scfg["k_trail"]))
            if lows[i] <= stop:
                stopped_high_water = high_water
                exit_px = min(stop, opens[i])
                stopped_atr = atr[i] if np.isfinite(atr[i]) else signal_atr
                record_exit(i, exit_px, "stop")
                stop_bars.append(i)
                post_stop = {
                    "bar": i,
                    "fill": exit_px,
                    "atr": stopped_atr,
                    "high_water": stopped_high_water,
                }
            elif (mode == "SCRAPE" and entry_chop
                  and highs[i] >= entry + signal_atr):
                record_exit(i, max(entry + signal_atr, opens[i]), "profit_target")
                post_stop = None
            else:
                high_water = max(high_water, highs[i])
                stop = max(stop, high_water - k_effective * atr[i])
                holding_bars += 1
                if mode == "SCRAPE" and entry_chop and holding_bars >= 6:
                    pending_time_exit = True

        # A contract switch invalidates any price anchor from the old contract.
        if not pos and rolls[i]:
            post_stop = None

        if (not pos and not pending and i > int(scfg["donchian_n"])):
            fire = bool(normal_signal[i])
            kind = "D20" if fire else ""

            fast_rule = h1
            if mode == "UNLOCK_SC":
                fast_rule = {"kind": "SC", "k": 10, "horizon": 24}
            elif mode == "UNLOCK_ATR":
                fast_rule = {"kind": "ATR", "m": 0.5, "horizon": 24}

            if (not fire and fast_rule is not None and post_stop is not None
                    and gate[i]):
                unlocked = not (mode in ("UNLOCK_SC", "UNLOCK_ATR")
                                and is_chop(i))
                if unlocked and _fast_rule(i, fast_rule, closes, atr, feats,
                                           post_stop):
                    fire = True
                    kind = "H1_" + fast_rule["kind"]

            chop_now = is_chop(i)
            if fire and mode == "BLOCK" and chop_now:
                fire = False
            if fire:
                target_qty = int(week_qty(stamps[i]))
                if target_qty > 0:
                    pending = True
                    pending_qty = target_qty
                    pending_kind = kind
                    pending_chop = chop_now

    columns = ["entry_time", "exit_time", "sym", "entry_px", "exit_px",
               "points", "reason", "dir", "qty", "entry_kind", "entry_chop"]
    trades = pd.DataFrame(rows, columns=columns)
    return trades[trades["entry_time"] >= rg.START].reset_index(drop=True)
