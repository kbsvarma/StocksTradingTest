"""Corrected day-session satellite engine used by the Codex verification."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class DayCell:
    start_hour: int
    close_hour: int
    trail_k: float
    prior_bars: int = 2
    buffer_atr: float = 0.0

    @property
    def name(self) -> str:
        return (f"s{self.start_hour}_x{self.close_hour}_k{self.trail_k:g}"
                f"_p{self.prior_bars}_b{self.buffer_atr:g}")


TRADE_COLUMNS = [
    "entry_time", "exit_time", "sym", "entry_px", "exit_px", "points",
    "reason", "dir", "qty", "signal_score", "session",
]


def simulate_day(
    bars: pd.DataFrame,
    sym: str,
    atr: np.ndarray,
    vol_ok: np.ndarray,
    benched: np.ndarray,
    cell: DayCell,
    start: pd.Timestamp,
) -> pd.DataFrame:
    """Simulate one-contract, one-trade-per-session long breakouts.

    Bars are left-labelled. ``close_hour=16`` therefore admits labels through
    15:00 and liquidates at that final bar's close. Each session is processed
    independently, making an overnight carry impossible even on short/missing
    sessions.
    """
    if not (0 <= cell.start_hour < cell.close_hour <= 24):
        raise ValueError("invalid session hours")
    if cell.prior_bars < 1 or cell.trail_k <= 0:
        raise ValueError("prior_bars and trail_k must be positive")

    et = bars.index.tz_convert("America/New_York")
    eligible = ((et.hour >= cell.start_hour) & (et.hour < cell.close_hour))
    by_date: dict[object, list[int]] = {}
    for i in np.flatnonzero(eligible):
        by_date.setdefault(et[i].date(), []).append(int(i))

    opens = bars["open"].to_numpy()
    highs = bars["high"].to_numpy()
    lows = bars["low"].to_numpy()
    closes = bars["close"].to_numpy()
    rolls = bars["roll"].to_numpy(dtype=bool)
    ts = bars.index
    rows: list[tuple] = []

    for session, indices in by_date.items():
        if not indices:
            continue
        # A resampled roll hour can contain observations from both contracts.
        # Skip the entire affected daytime session instead of comparing a
        # new-contract breakout with an old-contract session high.
        if any(rolls[i] for i in indices):
            continue
        session_high = -np.inf
        prior_count = 0
        pending: tuple[float, float] | None = None  # signal ATR, score
        position = False
        entry = high_water = stop = np.nan
        entry_time = None
        traded = False
        last_i = indices[-1]

        for offset, i in enumerate(indices):
            # A roll flag identifies the first bar of a new contract. A signal
            # from the prior contract must never fill at this bar's open.
            if pending is not None and rolls[i]:
                pending = None

            if pending is not None:
                signal_atr, signal_score = pending
                pending = None
                if np.isfinite(signal_atr):
                    position = True
                    entry = high_water = float(opens[i])
                    stop = entry - cell.trail_k * signal_atr
                    entry_time = ts[i]

            if position and rolls[i]:
                # This can only be a position opened earlier in the same
                # session. Use the last old-contract close, never the new open.
                x = float(closes[i - 1]) if i > 0 else float(opens[i])
                rows.append((entry_time, ts[i], sym, entry, x, x - entry,
                             "roll", 1, 1, signal_score, str(session)))
                position = False

            if position:
                if lows[i] <= stop:
                    x = float(min(stop, opens[i]))
                    rows.append((entry_time, ts[i], sym, entry, x, x - entry,
                                 "stop", 1, 1, signal_score, str(session)))
                    position = False
                elif i == last_i:
                    x = float(closes[i])
                    rows.append((entry_time, ts[i], sym, entry, x, x - entry,
                                 "eod", 1, 1, signal_score, str(session)))
                    position = False
                else:
                    high_water = max(high_water, float(highs[i]))
                    if np.isfinite(atr[i]):
                        stop = max(stop, high_water - cell.trail_k * atr[i])

            # The next row must be another eligible bar in this same session.
            has_next = offset + 1 < len(indices) and indices[offset + 1] == i + 1
            if (not position and pending is None and not traded and has_next
                    and benched[i] and prior_count >= cell.prior_bars
                    and vol_ok[i] and np.isfinite(atr[i])):
                threshold = session_high + cell.buffer_atr * atr[i]
                if closes[i] > threshold:
                    score = float((closes[i] - session_high) / atr[i])
                    pending = (float(atr[i]), score)
                    traded = True

            session_high = max(session_high, float(highs[i]))
            prior_count += 1

        # No path may escape the date. A pending order on the last eligible
        # bar is prevented by ``has_next``; a position is force-closed above.
        if position or pending is not None:
            raise AssertionError(f"{sym} {session}: session leaked state")

    out = pd.DataFrame(rows, columns=TRADE_COLUMNS)
    if out.empty:
        return out
    out = out[out["entry_time"] >= start].reset_index(drop=True)
    entry_dates = out["entry_time"].dt.tz_convert("America/New_York").dt.date
    exit_dates = out["exit_time"].dt.tz_convert("America/New_York").dt.date
    if not (entry_dates == exit_dates).all():
        raise AssertionError("overnight trade emitted")
    return out


def exposure_summary(core: pd.DataFrame, sat: pd.DataFrame) -> dict:
    """Conservative concurrent counts; same-label entries precede exits."""
    events: list[tuple[pd.Timestamp, int, str]] = []
    for lane, trades in (("core", core), ("sat", sat)):
        for tr in trades.itertuples():
            q = int(tr.qty)
            events.extend(((tr.entry_time, q, lane), (tr.exit_time, -q, lane)))
    core_q = sat_q = max_total = max_core = max_sat = 0
    # Timestamps label whole bars, so an entry and exit carrying the same label
    # can still coexist intrabar. Process entries first: this is conservative
    # for peak capacity and keeps a same-bar round trip internally valid.
    for _, delta, lane in sorted(events, key=lambda x: (x[0], x[1] < 0)):
        if lane == "core":
            core_q += delta
        else:
            sat_q += delta
        if min(core_q, sat_q) < 0:
            raise AssertionError("negative exposure while replaying trades")
        max_core = max(max_core, core_q)
        max_sat = max(max_sat, sat_q)
        max_total = max(max_total, core_q + sat_q)
    return {"max_core": max_core, "max_sat": max_sat,
            "max_combined": max_total}
