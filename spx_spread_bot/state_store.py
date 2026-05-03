from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from models import LegDirection, OpenPosition, PositionLeg, RuntimeState


class RuntimeStateStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def load(self) -> RuntimeState:
        if not self.path.exists():
            return RuntimeState()
        raw = self.path.read_text(encoding="utf-8")
        if not raw.strip():
            return RuntimeState()

        import json

        payload: dict[str, Any] = json.loads(raw)
        open_positions: list[OpenPosition] = []
        raw_open_positions = payload.get("open_positions")
        if isinstance(raw_open_positions, list):
            for row in raw_open_positions:
                if isinstance(row, dict):
                    open_positions.append(self._parse_open_position(row))

        # Backward compatibility with single-position payload.
        if not open_positions:
            open_pos_payload = payload.get("open_position")
            if isinstance(open_pos_payload, dict):
                open_positions = [self._parse_open_position(open_pos_payload)]

        open_pos = open_positions[0] if open_positions else None
        raw_attempted = payload.get("attempted_strategies_today", [])
        if not isinstance(raw_attempted, list):
            raw_attempted = []

        return RuntimeState(
            current_trading_date=payload.get("current_trading_date", ""),
            current_week_key=str(payload.get("current_week_key", "")),
            current_month_key=str(payload.get("current_month_key", "")),
            trade_taken_today=bool(payload.get("trade_taken_today", False)),
            attempted_strategies_today=[str(s).upper() for s in raw_attempted if str(s)],
            open_positions=open_positions,
            open_position=open_pos,
            weekly_pnl=float(payload.get("weekly_pnl", 0.0)),
            monthly_pnl=float(payload.get("monthly_pnl", 0.0)),
            total_trades=int(payload.get("total_trades", 0)),
            wins=int(payload.get("wins", 0)),
            losses=int(payload.get("losses", 0)),
            skip_reason_today=str(payload.get("skip_reason_today", "")),
            updated_at=str(payload.get("updated_at", "")),
        )

    def _parse_open_position(self, payload: dict[str, Any]) -> OpenPosition:
        entry_ts = payload.get("entry_ts")
        if isinstance(entry_ts, str):
            entry_ts = datetime.fromisoformat(entry_ts)
        if not isinstance(entry_ts, datetime):
            entry_ts = datetime.now(UTC)

        raw_legs = payload.get("legs")
        legs: list[PositionLeg] = []
        if isinstance(raw_legs, list):
            for leg in raw_legs:
                if not isinstance(leg, dict):
                    continue
                direction = str(leg.get("direction", LegDirection.SHORT.value)).upper()
                if "." in direction:
                    direction = direction.rsplit(".", 1)[-1]
                if direction not in {LegDirection.LONG.value, LegDirection.SHORT.value}:
                    direction = LegDirection.SHORT.value
                legs.append(
                    PositionLeg(
                        right=str(leg.get("right", "P")).upper(),
                        strike=float(leg.get("strike", 0.0)),
                        direction=LegDirection(direction),
                        quantity=int(leg.get("quantity", 1)),
                        con_id=int(leg.get("con_id", 0)),
                    )
                )

        # Backward compatibility for legacy two-leg put spread payloads.
        if not legs:
            short_con_id = int(payload.get("short_con_id", 0))
            long_con_id = int(payload.get("long_con_id", 0))
            short_strike = float(payload.get("short_put_strike", payload.get("short_strike", 0.0)))
            long_strike = float(payload.get("long_put_strike", payload.get("long_strike", 0.0)))

            if short_con_id:
                legs.append(
                    PositionLeg(
                        right="P",
                        strike=short_strike,
                        direction=LegDirection.SHORT,
                        quantity=1,
                        con_id=short_con_id,
                    )
                )
            if long_con_id:
                legs.append(
                    PositionLeg(
                        right="P",
                        strike=long_strike,
                        direction=LegDirection.LONG,
                        quantity=1,
                        con_id=long_con_id,
                    )
                )

        return OpenPosition(
            strategy=str(payload.get("strategy", "PUT_BWB")),
            entry_ts=entry_ts,
            expiry=str(payload.get("expiry", "")),
            legs=legs,
            contracts=int(payload.get("contracts", 0)),
            entry_credit=float(payload.get("entry_credit", 0.0)),
            entry_spx=float(payload.get("entry_spx", 0.0)),
            entry_vix=float(payload.get("entry_vix", 0.0)),
            stop_price=float(payload.get("stop_price", 0.0)),
            profit_target_price=float(payload.get("profit_target_price", 0.0)),
            combo_order_id=int(payload.get("combo_order_id", 0)),
            short_put_strike=float(payload.get("short_put_strike", payload.get("short_strike", 0.0))),
            short_call_strike=float(payload.get("short_call_strike", 0.0)),
            max_loss_per_contract=float(payload.get("max_loss_per_contract", 0.0)),
            stop_order_id=int(payload["stop_order_id"]) if payload.get("stop_order_id") else None,
            profit_order_id=int(payload["profit_order_id"]) if payload.get("profit_order_id") else None,
            eod_distance_safe_confirmed=bool(payload.get("eod_distance_safe_confirmed", False)),
            status=str(payload.get("status", "OPEN")).split(".")[-1],
        )

    def save(self, state: RuntimeState) -> None:
        import json

        # Keep legacy single-position mirror for dashboard/backward compatibility.
        state.open_position = state.open_positions[0] if state.open_positions else None

        payload = asdict(state)
        for idx, pos in enumerate(state.open_positions):
            if isinstance(pos.entry_ts, datetime):
                payload["open_positions"][idx]["entry_ts"] = pos.entry_ts.isoformat()
        if state.open_position and isinstance(state.open_position.entry_ts, datetime):
            payload["open_position"]["entry_ts"] = state.open_position.entry_ts.isoformat()
        payload["updated_at"] = datetime.now(UTC).isoformat()
        self.path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
