# strategy.py — ORB range construction, locking, and signal generation.
# No broker dependency. Fully testable in isolation via replay_test.py.

from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional

import config


# ── Data containers ────────────────────────────────────────────────────────────

@dataclass
class Bar:
    timestamp: datetime   # bar open time, ET
    open: float
    high: float
    low: float
    close: float
    volume: int


@dataclass
class OpeningRange:
    high: float
    low: float
    width: float          # high - low, rounded
    bar_count: int
    locked_at: datetime


@dataclass
class TradeContext:
    """Snapshot of all conditions at the moment of entry. Used for logging and analysis."""
    direction: str           # 'LONG' | 'SHORT'
    entry_price: float       # actual fill price (updated after fill)
    stop_price: float
    target_price: float
    trigger_bar_timestamp: datetime
    trigger_bar_close: float
    range: OpeningRange
    signal_eval_count: int   # how many bars were evaluated before this signal
    vix_at_entry: Optional[float]
    gap_pct_at_entry: float
    submitted_at: Optional[datetime] = None
    filled_at: Optional[datetime] = None
    closed_at: Optional[datetime] = None
    close_price: Optional[float] = None
    close_reason: Optional[str] = None


# ── Strategy engine ────────────────────────────────────────────────────────────

class ORBStrategy:
    """
    Stateful single-day ORB engine.
    Call reset() at the start of each session.

    Exit hierarchy (checked in _handle_in_trade, first match wins):
      1. VWAP_STOP  — session VWAP crossed against position (dynamic trailing stop)
      2. HARD_CLOSE — time-based close at config.HARD_CLOSE_TIME
      3. STOP       — broker-managed stop loss (bracket leg, at opposite range edge)
      4. TARGET     — broker-managed target (entry ± SAFETY_TARGET_MULT × range_width)

    WARNING_SIGNAL_EXIT is disabled (config.WARNING_SIGNAL_EXIT=False).
    Replay 2019-2026: 294/310 exits were WARNING_EXIT → Sharpe -1.25.
    Without it: Sharpe +0.78.
    """

    def __init__(self) -> None:
        self._range_bars: List[Bar] = []
        self._opening_range: Optional[OpeningRange] = None
        self._range_locked: bool = False
        self._signal_fired: bool = False
        self._eval_count: int = 0
        self._allowed_direction: Optional[str] = None  # None=both; 'LONG'/'SHORT'=filtered

        # VWAP accumulator — updated every bar from RANGE_START_TIME onwards
        self._session_pv: float = 0.0   # Σ(typical_price × volume)
        self._session_vol: int = 0       # Σ(volume)
        self._session_vwap: float = 0.0  # current session VWAP

        # In-trade state for dynamic exits
        self._trade_direction: Optional[str] = None
        self._bars_in_trade: int = 0         # bars elapsed since entry fill
        self._half_ext_reached: bool = False  # True once price hit 50% extension

        # Daily ATR (set at startup for ATR-based width filter)
        self._daily_atr: Optional[float] = None

    # ── Session lifecycle ──────────────────────────────────────────────────────

    def reset(self) -> None:
        self._range_bars = []
        self._opening_range = None
        self._range_locked = False
        self._signal_fired = False
        self._eval_count = 0
        self._session_pv = 0.0
        self._session_vol = 0
        self._session_vwap = 0.0
        self._trade_direction = None
        self._bars_in_trade = 0
        self._half_ext_reached = False
        # Note: _allowed_direction and _daily_atr persist across reset() so
        # bot.py sets them once at startup. Call set_allowed_direction(None) /
        # set_daily_atr(None) explicitly to clear them.

    def set_allowed_direction(self, direction: Optional[str]) -> None:
        """
        Restrict signal firing to one direction (trend filter).
        Pass None to allow both LONG and SHORT signals (no filter).
        Pass 'LONG' to only fire on upside breakouts.
        Pass 'SHORT' to only fire on downside breakouts.
        Does NOT call reset() — set this before the session begins.
        """
        self._allowed_direction = direction

    def set_daily_atr(self, atr: Optional[float]) -> None:
        """Store the 20-day ATR for use in the range width filter."""
        self._daily_atr = atr

    # ── VWAP accumulator ──────────────────────────────────────────────────────

    def update_vwap(self, bar: Bar) -> float:
        """
        Update the rolling session VWAP with a completed bar.
        Must be called for every bar from RANGE_START_TIME onwards,
        including range-building bars and bars while waiting for entry.
        Returns the current VWAP.
        """
        typical = (bar.high + bar.low + bar.close) / 3.0
        vol = max(bar.volume, 1)   # guard against zero-volume bars
        self._session_pv += typical * vol
        self._session_vol += vol
        self._session_vwap = self._session_pv / self._session_vol
        return self._session_vwap

    # ── Trade state tracking ───────────────────────────────────────────────────

    def set_entry(self, direction: str, entry_price: float) -> None:
        """
        Record the entry direction immediately after fill confirmation.
        Resets in-trade counters. Must be called before check_vwap_stop
        or check_warning_signal will do anything.
        """
        self._trade_direction = direction
        self._bars_in_trade = 0
        self._half_ext_reached = False

    def increment_trade_bar(self) -> None:
        """Increment the bars-in-trade counter. Call once per bar while in trade."""
        self._bars_in_trade += 1

    # ── Dynamic exit checks ────────────────────────────────────────────────────

    def check_vwap_stop(self, bar: Bar) -> bool:
        """
        Returns True if the bar's close has crossed the session VWAP against
        the open position — triggering a VWAP trailing stop exit.

        Requires VWAP_TRAILING_STOP=True and at least VWAP_MIN_BARS bars in trade
        (avoids exiting on the first bar when VWAP and price are nearly identical).
        """
        if not config.VWAP_TRAILING_STOP:
            return False
        if self._trade_direction is None:
            return False
        if self._session_vwap == 0.0:
            return False
        if self._bars_in_trade < config.VWAP_MIN_BARS:
            return False

        if self._trade_direction == "LONG":
            return bar.close < self._session_vwap
        else:  # SHORT
            return bar.close > self._session_vwap

    def check_warning_signal(self, bar: Bar) -> bool:
        """
        Warning signal exit for LONG trades.
        Returns True if:
          - Price has NOT yet reached 50% of range extension above range_high
          - AND current bar closes below 25% extension above range_high

        This detects a failed breakout early (continuation probability drops
        from 71.1% to 22.7% in this zone) and exits before a full retracement.

        Only applies to LONG trades. SHORT analog omitted (LONG_ONLY mode).
        """
        if not config.WARNING_SIGNAL_EXIT:
            return False
        if self._opening_range is None:
            return False
        if self._trade_direction != "LONG":
            return False
        if self._bars_in_trade < 1:
            return False

        half_ext = self._opening_range.high + 0.5 * self._opening_range.width
        quarter_ext = self._opening_range.high + 0.25 * self._opening_range.width

        # Track whether 50% extension was ever reached on this bar's high
        if bar.high >= half_ext:
            self._half_ext_reached = True

        # Only trigger if 50% extension was NEVER reached
        if not self._half_ext_reached and bar.close < quarter_ext:
            return True

        return False

    # ── Range construction ─────────────────────────────────────────────────────

    def add_range_bar(self, bar: Bar) -> None:
        """Append a bar to the opening range window. Call only during RANGE_BUILDING."""
        self._range_bars.append(bar)

    def lock_range(self, locked_at: datetime) -> Optional[OpeningRange]:
        """
        Freeze the opening range. Returns None if no bars were collected
        (e.g. bot started late), which should trigger HALTED.
        """
        if not self._range_bars:
            return None

        high = max(b.high for b in self._range_bars)
        low = min(b.low for b in self._range_bars)
        width = round(high - low, 4)

        self._opening_range = OpeningRange(
            high=high,
            low=low,
            width=width,
            bar_count=len(self._range_bars),
            locked_at=locked_at,
        )
        self._range_locked = True
        return self._opening_range

    # ── Signal evaluation ──────────────────────────────────────────────────────

    def evaluate_signal(self, bar: Bar) -> Optional[str]:
        """
        Check a completed bar for a breakout. Returns 'LONG', 'SHORT', or None.
        Fires at most once per day (first allowed signal wins).

        Trend filter: if _allowed_direction is set, signals in the opposite direction
        are silently skipped — _signal_fired is NOT set, so the allowed direction can
        still fire later if price reverses back through the range.
        """
        if not self._range_locked or self._opening_range is None:
            return None
        if self._signal_fired:
            return None

        self._eval_count += 1

        # Breakout buffer: bar close must exceed range edge by at least
        # BREAKOUT_BUFFER_TICKS ticks to avoid single-tick false breaks.
        buffer = config.BREAKOUT_BUFFER_TICKS * config.TICK_SIZE

        raw_signal: Optional[str] = None
        if bar.close > self._opening_range.high + buffer:
            raw_signal = "LONG"
        elif bar.close < self._opening_range.low - buffer:
            raw_signal = "SHORT"

        if raw_signal is None:
            return None

        # Trend filter: skip if signal direction is blocked
        if self._allowed_direction and raw_signal != self._allowed_direction:
            return None   # do NOT set _signal_fired so the allowed side can still trigger

        self._signal_fired = True
        return raw_signal

    # ── Level computation ──────────────────────────────────────────────────────

    def compute_levels(
        self, direction: str, entry_price: float
    ) -> tuple[float, float]:
        """
        Return (stop_price, target_price) given direction and actual fill price.
        Both rounded to the nearest MES tick.

        Stop: at the opposite edge of the opening range (standard ORB stop).
        Target: entry ± SAFETY_TARGET_MULT × width (default 3×).
          Validated 3:1 R:R configuration (research/live parity confirmed).
          Real exits also come from VWAP_STOP and HARD_CLOSE — whichever
          fires first wins.
        """
        if self._opening_range is None:
            raise RuntimeError("Cannot compute levels before range is locked")

        w = self._opening_range.width

        if direction == "LONG":
            stop = _round_tick(self._opening_range.low)
            target = _round_tick(entry_price + config.SAFETY_TARGET_MULT * w)
        else:
            stop = _round_tick(self._opening_range.high)
            target = _round_tick(entry_price - config.SAFETY_TARGET_MULT * w)

        return stop, target

    # ── Diagnostics ────────────────────────────────────────────────────────────

    def range_bar_summary(self) -> List[dict]:
        return [
            {
                "time": b.timestamp.strftime("%H:%M"),
                "open": b.open,
                "high": b.high,
                "low": b.low,
                "close": b.close,
                "volume": b.volume,
            }
            for b in self._range_bars
        ]

    @property
    def opening_range(self) -> Optional[OpeningRange]:
        return self._opening_range

    @property
    def range_locked(self) -> bool:
        return self._range_locked

    @property
    def eval_count(self) -> int:
        return self._eval_count

    @property
    def session_vwap(self) -> float:
        return self._session_vwap

    @property
    def bars_in_trade(self) -> int:
        return self._bars_in_trade

    @property
    def daily_atr(self) -> Optional[float]:
        return self._daily_atr


# ── Helpers ────────────────────────────────────────────────────────────────────

def _round_tick(price: float) -> float:
    tick = config.TICK_SIZE
    return round(round(price / tick) * tick, 4)
