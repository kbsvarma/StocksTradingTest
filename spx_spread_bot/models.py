from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional


class Decision(str, Enum):
    ENTER = "ENTER"
    SKIP = "SKIP"


class StrategyType(str, Enum):
    BULL_PUT_SPREAD = "BULL_PUT_SPREAD"
    PUT_BWB = "PUT_BWB"
    IRON_CONDOR = "IRON_CONDOR"
    IRON_FLY = "IRON_FLY"


class LegDirection(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"


class ExitReason(str, Enum):
    PROFIT_TARGET = "profit_target"
    STOP_LOSS = "stop_loss"
    EOD_DISTANCE_SAFE = "expiry"
    EOD_FORCE_CLOSE = "eod_force_close"
    FRIDAY_FORCE_CLOSE = "friday_force_close"
    MANUAL = "manual"
    SAFETY_FLATTEN = "safety_flatten"


class PositionStatus(str, Enum):
    FLAT = "FLAT"
    OPEN = "OPEN"
    EXITING = "EXITING"


@dataclass(slots=True)
class SpreadQuote:
    bid: float
    ask: float
    mid: float
    ts: datetime


@dataclass(slots=True)
class OptionLegSpec:
    right: str
    strike: float
    direction: LegDirection
    quantity: int = 1


@dataclass(slots=True)
class PositionLeg:
    right: str
    strike: float
    direction: LegDirection
    quantity: int
    con_id: int


@dataclass(slots=True)
class CandidateSpread:
    strategy: StrategyType
    expiry: str
    dte: int
    legs: list[OptionLegSpec]
    otm_pct: float
    target_level: float
    short_put_strike: float = 0.0
    long_put_strike: float = 0.0
    short_call_strike: float = 0.0
    long_call_strike: float = 0.0
    max_loss_per_contract: float = 0.0
    notes: str = ""
    quote: Optional[SpreadQuote] = None

    @property
    def short_strike(self) -> float:
        # Legacy alias used by older display paths.
        return self.short_put_strike

    @property
    def long_strike(self) -> float:
        # Legacy alias used by older display paths.
        return self.long_put_strike


@dataclass(slots=True)
class SignalResult:
    decision: Decision
    reason: str
    spx_price: float
    vix_price: float
    candidate: Optional[CandidateSpread] = None
    candidates: list[CandidateSpread] = field(default_factory=list)
    contracts: int = 0
    estimated_margin: float = 0.0


@dataclass(slots=True)
class OpenPosition:
    strategy: str
    entry_ts: datetime
    expiry: str
    legs: list[PositionLeg]
    contracts: int
    entry_credit: float
    entry_spx: float
    entry_vix: float
    stop_price: float
    profit_target_price: float
    combo_order_id: int
    short_put_strike: float = 0.0
    short_call_strike: float = 0.0
    max_loss_per_contract: float = 0.0
    stop_order_id: Optional[int] = None
    profit_order_id: Optional[int] = None
    eod_distance_safe_confirmed: bool = False
    status: PositionStatus = PositionStatus.OPEN

    @property
    def short_con_id(self) -> int:
        for leg in self.legs:
            if leg.direction == LegDirection.SHORT:
                return leg.con_id
        return 0

    @property
    def long_con_id(self) -> int:
        for leg in self.legs:
            if leg.direction == LegDirection.LONG:
                return leg.con_id
        return 0

    @property
    def short_strike(self) -> float:
        return self.short_put_strike

    @property
    def long_strike(self) -> float:
        # This is intentionally the nearest long put hedge for legacy dashboard rows.
        put_longs = [leg.strike for leg in self.legs if leg.direction == LegDirection.LONG and leg.right == "P"]
        return min(put_longs) if put_longs else 0.0


@dataclass(slots=True)
class RuntimeState:
    current_trading_date: str = ""
    current_week_key: str = ""
    current_month_key: str = ""
    trade_taken_today: bool = False
    stop_loss_today: bool = False          # True if a stop-loss fired today — no re-entry allowed
    attempted_strategies_today: list[str] = field(default_factory=list)
    open_positions: list[OpenPosition] = field(default_factory=list)
    open_position: Optional[OpenPosition] = None
    weekly_pnl: float = 0.0
    monthly_pnl: float = 0.0
    total_trades: int = 0
    wins: int = 0
    losses: int = 0
    skip_reason_today: str = ""
    updated_at: str = ""


@dataclass(slots=True)
class TradeRecord:
    date: str
    strategy: str
    legs: str
    entry_time: str
    spx_price_at_entry: float
    vix_at_entry: float
    short_put_strike: float
    long_put_strike: float
    short_call_strike: float
    long_call_strike: float
    credit_received: float
    contracts: int
    exit_time: str
    exit_price: float
    pnl_per_contract: float
    total_pnl: float
    win_loss: str
    exit_reason: str
    notes: str = ""


@dataclass(slots=True)
class TickSnapshot:
    ts: datetime
    symbol: str
    expiry: str
    strike: float
    right: str
    bid: float
    ask: float
    last: float
    model: float
    source: str = "stream"
    extras: dict[str, float] = field(default_factory=dict)
