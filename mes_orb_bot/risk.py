# risk.py — Pre-trade filters, daily P&L tracking, and loss-limit enforcement.

from dataclasses import dataclass
from typing import Optional

import config


# ── Result containers ──────────────────────────────────────────────────────────

@dataclass
class FilterResult:
    passed: bool
    reason: str
    value: Optional[float] = None   # the measured value that was tested


@dataclass
class DailyPnL:
    gross_pnl: float = 0.0
    commissions: float = 0.0
    trade_count: int = 0
    win_count: int = 0
    loss_count: int = 0

    @property
    def net_pnl(self) -> float:
        return self.gross_pnl - self.commissions

    @property
    def win_rate(self) -> Optional[float]:
        if self.trade_count == 0:
            return None
        return self.win_count / self.trade_count

    def record_trade(self, gross_dollars: float) -> None:
        self.gross_pnl += gross_dollars
        # Round-trip commission scales with number of contracts
        self.commissions += config.COMMISSION_PER_CONTRACT * 2 * config.CONTRACTS
        self.trade_count += 1
        if gross_dollars > 0:
            self.win_count += 1
        else:
            self.loss_count += 1

    def summary(self) -> str:
        wr = f"{self.win_rate:.1%}" if self.win_rate is not None else "N/A"
        return (
            f"trades={self.trade_count} wins={self.win_count} losses={self.loss_count} "
            f"winRate={wr} gross=${self.gross_pnl:+.2f} "
            f"commission=${self.commissions:.2f} net=${self.net_pnl:+.2f}"
        )


# ── Risk manager ───────────────────────────────────────────────────────────────

class RiskManager:
    """
    Stateful per-session risk object.
    Call reset() at the start of each session.
    """

    def __init__(self) -> None:
        self._daily_pnl = DailyPnL()
        self._vix_value: Optional[float] = None
        self._vix_available: bool = False
        self._prev_close: Optional[float] = None
        self._today_open: Optional[float] = None
        self._daily_atr: Optional[float] = None   # 20-day ATR for width filter

    def reset(self) -> None:
        self._daily_pnl = DailyPnL()
        self._vix_value = None
        self._vix_available = False
        self._prev_close = None
        self._today_open = None
        # _daily_atr persists across reset — set once at startup via set_daily_atr()

    # ── Setters ────────────────────────────────────────────────────────────────

    def set_vix(self, value: Optional[float]) -> None:
        self._vix_value = value
        self._vix_available = value is not None and value > 0

    def set_prev_close(self, price: float) -> None:
        self._prev_close = price

    def set_today_open(self, price: float) -> None:
        self._today_open = price

    def set_daily_atr(self, atr: Optional[float]) -> None:
        """Store the 20-day ATR for use in check_range_width."""
        self._daily_atr = atr

    # ── Filters ────────────────────────────────────────────────────────────────

    def check_vix(self) -> FilterResult:
        if not self._vix_available:
            if config.ALLOW_TRADING_WITHOUT_VIX:
                return FilterResult(
                    passed=True,
                    reason="VIX unavailable; ALLOW_TRADING_WITHOUT_VIX=True",
                )
            return FilterResult(
                passed=False,
                reason="VIX data unavailable and ALLOW_TRADING_WITHOUT_VIX=False",
            )

        if self._vix_value >= config.VIX_THRESHOLD:  # type: ignore[operator]
            return FilterResult(
                passed=False,
                reason=f"VIX {self._vix_value:.2f} >= threshold {config.VIX_THRESHOLD}",
                value=self._vix_value,
            )
        return FilterResult(
            passed=True,
            reason=f"VIX {self._vix_value:.2f} < threshold {config.VIX_THRESHOLD}",
            value=self._vix_value,
        )

    def check_gap(self) -> FilterResult:
        if self._prev_close is None:
            if config.ALLOW_TRADING_WITHOUT_PREV_CLOSE:
                return FilterResult(
                    passed=True,
                    reason="prev_close unavailable; ALLOW_TRADING_WITHOUT_PREV_CLOSE=True — gap filter skipped",
                )
            return FilterResult(
                passed=False,
                reason="prev_close unavailable and ALLOW_TRADING_WITHOUT_PREV_CLOSE=False",
            )
        if self._today_open is None:
            return FilterResult(
                passed=False,
                reason="today_open not yet set; cannot compute gap",
            )

        gap_pct = abs((self._today_open - self._prev_close) / self._prev_close)

        if gap_pct >= config.GAP_THRESHOLD_PCT:
            return FilterResult(
                passed=False,
                reason=(
                    f"Gap {gap_pct:.4%} >= threshold {config.GAP_THRESHOLD_PCT:.4%} "
                    f"(open={self._today_open:.2f} prevClose={self._prev_close:.2f})"
                ),
                value=gap_pct,
            )
        return FilterResult(
            passed=True,
            reason=(
                f"Gap {gap_pct:.4%} < threshold {config.GAP_THRESHOLD_PCT:.4%}"
            ),
            value=gap_pct,
        )

    def check_range_width(self, width: float) -> FilterResult:
        """
        Validate opening range width.

        Primary: ATR-based filter (ATR_MIN_WIDTH_MULT × ATR to ATR_MAX_WIDTH_MULT × ATR).
          - Narrow range (<0.3× ATR): 62.9% continuation — acceptable but skip per research
          - Optimal (0.3–0.9× ATR): 70.7% continuation rate
          - Extreme wide (>0.9× ATR): only 22.3% extension probability → skip

        Fallback: fixed point limits when ATR data is unavailable.
        """
        if self._daily_atr is not None and self._daily_atr > 0:
            min_w = config.ATR_MIN_WIDTH_MULT * self._daily_atr
            max_w = config.ATR_MAX_WIDTH_MULT * self._daily_atr
            atr_label = f"ATR={self._daily_atr:.2f}"
            if width < min_w:
                return FilterResult(
                    passed=False,
                    reason=(
                        f"Range width {width:.2f} pts < {config.ATR_MIN_WIDTH_MULT}×{atr_label} "
                        f"= {min_w:.2f} pts (ATR filter: range too narrow)"
                    ),
                    value=width,
                )
            if width > max_w:
                return FilterResult(
                    passed=False,
                    reason=(
                        f"Range width {width:.2f} pts > {config.ATR_MAX_WIDTH_MULT}×{atr_label} "
                        f"= {max_w:.2f} pts (ATR filter: range too wide)"
                    ),
                    value=width,
                )
            return FilterResult(
                passed=True,
                reason=(
                    f"Range width {width:.2f} pts within ATR band "
                    f"[{min_w:.2f}, {max_w:.2f}] ({atr_label})"
                ),
                value=width,
            )

        # Fallback: fixed point limits (no ATR data available)
        if width < config.MIN_OPENING_RANGE_POINTS:
            return FilterResult(
                passed=False,
                reason=(
                    f"Range width {width:.2f} pts < min {config.MIN_OPENING_RANGE_POINTS} pts "
                    "(fixed fallback — ATR unavailable)"
                ),
                value=width,
            )
        if width > config.MAX_OPENING_RANGE_POINTS:
            return FilterResult(
                passed=False,
                reason=(
                    f"Range width {width:.2f} pts > max {config.MAX_OPENING_RANGE_POINTS} pts "
                    "(fixed fallback — ATR unavailable)"
                ),
                value=width,
            )
        return FilterResult(
            passed=True,
            reason=(
                f"Range width {width:.2f} pts within "
                f"[{config.MIN_OPENING_RANGE_POINTS}, {config.MAX_OPENING_RANGE_POINTS}] "
                "(fixed fallback — ATR unavailable)"
            ),
            value=width,
        )

    def check_daily_loss_limit(self) -> FilterResult:
        net = self._daily_pnl.net_pnl
        if net <= config.DAILY_LOSS_LIMIT:
            return FilterResult(
                passed=False,
                reason=f"Daily net P&L ${net:+.2f} <= limit ${config.DAILY_LOSS_LIMIT:+.2f}",
                value=net,
            )
        return FilterResult(
            passed=True,
            reason=f"Daily net P&L ${net:+.2f} above limit",
            value=net,
        )

    # ── P&L recording ──────────────────────────────────────────────────────────

    def record_trade(self, gross_pnl_dollars: float) -> DailyPnL:
        self._daily_pnl.record_trade(gross_pnl_dollars)
        return self._daily_pnl

    # ── Properties ─────────────────────────────────────────────────────────────

    @property
    def daily_pnl(self) -> DailyPnL:
        return self._daily_pnl

    @property
    def vix_value(self) -> Optional[float]:
        return self._vix_value

    @property
    def today_open(self) -> Optional[float]:
        return self._today_open

    @property
    def prev_close(self) -> Optional[float]:
        return self._prev_close

    @property
    def daily_atr(self) -> Optional[float]:
        return self._daily_atr
