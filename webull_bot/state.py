"""Simple JSON state store for the Webull bot."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Optional


@dataclass
class OpenPosition:
    symbol: str
    expiry: str                 # YYYY-MM-DD
    short_strike: float
    long_strike: float
    quantity: int
    entry_credit: float         # net credit received per contract (SPX points)
    stop_price: float           # mark at which we close (2x entry_credit)
    entry_spx: float
    entry_vix: float
    entry_ts: str               # ISO timestamp
    client_order_id: str
    yf_options_symbol: str = "SPX"
    short_iid: str = ""         # Webull instrument_id of the short put leg
    long_iid: str = ""          # Webull instrument_id of the long put leg


@dataclass
class BotState:
    trading_date: str = ""              # YYYY-MM-DD of last trade date
    trade_taken_today: bool = False
    open_position: Optional[OpenPosition] = None
    total_trades: int = 0
    wins: int = 0
    losses: int = 0
    total_pnl: float = 0.0
    updated_at: str = ""


class StateStore:
    def __init__(self, path: str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def load(self) -> BotState:
        if not self.path.exists():
            return BotState()
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return BotState()

        pos_raw = raw.get("open_position")
        pos = None
        if isinstance(pos_raw, dict):
            try:
                pos = OpenPosition(**{k: v for k, v in pos_raw.items() if k in OpenPosition.__dataclass_fields__})
            except Exception:
                pos = None

        return BotState(
            trading_date=str(raw.get("trading_date", "")),
            trade_taken_today=bool(raw.get("trade_taken_today", False)),
            open_position=pos,
            total_trades=int(raw.get("total_trades", 0)),
            wins=int(raw.get("wins", 0)),
            losses=int(raw.get("losses", 0)),
            total_pnl=float(raw.get("total_pnl", 0.0)),
        )

    def save(self, state: BotState) -> None:
        state.updated_at = datetime.now(UTC).isoformat()
        payload = asdict(state)
        self.path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
