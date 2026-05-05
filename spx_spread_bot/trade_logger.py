from __future__ import annotations

import csv
import json
import logging
import re
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from typing import Any

from models import TickSnapshot, TradeRecord


class _IBNoiseFilter(logging.Filter):
    _noise = re.compile(r"\b(10089|10090)\b")

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003
        msg = record.getMessage()
        return self._noise.search(msg) is None


class BotLogger:
    def __init__(
        self,
        logs_dir: str,
        signal_events_file: str,
        order_events_file: str,
        tick_events_file: str,
        trade_csv_file: str,
        daily_summary_file: str,
    ) -> None:
        self.logs_dir = Path(logs_dir)
        self.logs_dir.mkdir(parents=True, exist_ok=True)

        self.signal_events_path = Path(signal_events_file)
        self.order_events_path = Path(order_events_file)
        self.tick_events_path = Path(tick_events_file)
        self.trade_csv_path = Path(trade_csv_file)
        self.daily_summary_path = Path(daily_summary_file)

        self.signal_events_path.parent.mkdir(parents=True, exist_ok=True)
        self.order_events_path.parent.mkdir(parents=True, exist_ok=True)
        self.tick_events_path.parent.mkdir(parents=True, exist_ok=True)
        self.trade_csv_path.parent.mkdir(parents=True, exist_ok=True)
        self.daily_summary_path.parent.mkdir(parents=True, exist_ok=True)

        self._lock = Lock()
        self.logger = logging.getLogger("spx_spread_bot")
        self.logger.setLevel(logging.INFO)
        self.logger.handlers.clear()

        formatter = logging.Formatter(
            fmt="%(asctime)s %(levelname)s %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S",
        )

        stream_handler = logging.StreamHandler()
        stream_handler.setFormatter(formatter)
        self.logger.addHandler(stream_handler)

        file_handler = logging.FileHandler(self.logs_dir / "system.log", encoding="utf-8")
        file_handler.setFormatter(formatter)
        self.logger.addHandler(file_handler)

        # Suppress repeated non-fatal IB market-data entitlement chatter
        # (10089/10090) while preserving all other IB diagnostics.
        ib_wrapper_logger = logging.getLogger("ib_insync.wrapper")
        ib_wrapper_logger.addFilter(_IBNoiseFilter())

    def info(self, message: str) -> None:
        self.logger.info(message)

    def warning(self, message: str) -> None:
        self.logger.warning(message)

    def error(self, message: str) -> None:
        self.logger.error(message)

    def signal_event(self, event_type: str, payload: dict[str, Any]) -> None:
        self._append_jsonl(self.signal_events_path, {"event": event_type, **payload})

    def order_event(self, event_type: str, payload: dict[str, Any]) -> None:
        self._append_jsonl(self.order_events_path, {"event": event_type, **payload})

    def tick_event_raw(self, event_type: str, payload: dict[str, Any]) -> None:
        """Write a free-form dict to tick_events (high-frequency log, rotated separately)."""
        self._append_jsonl(self.tick_events_path, {"event": event_type, **payload})

    def tick_event(self, tick: TickSnapshot) -> None:
        payload = {
            "ts": tick.ts.isoformat(),
            "symbol": tick.symbol,
            "expiry": tick.expiry,
            "strike": tick.strike,
            "right": tick.right,
            "bid": tick.bid,
            "ask": tick.ask,
            "last": tick.last,
            "model": tick.model,
            "source": tick.source,
            "extras": tick.extras,
        }
        self._append_jsonl(self.tick_events_path, payload)

    def append_trade(self, record: TradeRecord) -> None:
        row = {
            "Date": record.date,
            "Strategy": record.strategy,
            "Legs": record.legs,
            "Entry time": record.entry_time,
            "SPX price at entry": record.spx_price_at_entry,
            "VIX at entry": record.vix_at_entry,
            "Short put strike": record.short_put_strike,
            "Long put strike": record.long_put_strike,
            "Short call strike": record.short_call_strike,
            "Long call strike": record.long_call_strike,
            "Credit received": record.credit_received,
            "Contracts": record.contracts,
            "Exit time": record.exit_time,
            "Exit price": record.exit_price,
            "PnL per contract": record.pnl_per_contract,
            "Total PnL": record.total_pnl,
            "Win/Loss": record.win_loss,
            "Exit reason": record.exit_reason,
            "Notes": record.notes,
        }

        fieldnames = list(row.keys())
        with self._lock:
            write_header = not self.trade_csv_path.exists() or self.trade_csv_path.stat().st_size == 0
            if not write_header:
                try:
                    with self.trade_csv_path.open("r", newline="", encoding="utf-8") as handle:
                        reader = csv.reader(handle)
                        existing = next(reader, [])
                    if existing != fieldnames:
                        rotated = self.trade_csv_path.with_name(
                            f"{self.trade_csv_path.stem}.legacy.{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}.csv"
                        )
                        self.trade_csv_path.rename(rotated)
                        self.logger.warning(
                            f"trade csv header changed; rotated legacy file to {rotated.name}"
                        )
                        write_header = True
                except Exception:
                    write_header = True
            with self.trade_csv_path.open("a", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=fieldnames)
                if write_header:
                    writer.writeheader()
                writer.writerow(row)

    def daily_summary(self, payload: dict[str, Any]) -> None:
        line = f"{datetime.now(UTC).isoformat()} {json.dumps(payload, sort_keys=True)}\n"
        with self._lock:
            with self.daily_summary_path.open("a", encoding="utf-8") as handle:
                handle.write(line)

    def _append_jsonl(self, path: Path, payload: dict[str, Any]) -> None:
        payload_with_ts = {"ts": datetime.now(UTC).isoformat(), **payload}
        with self._lock:
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(payload_with_ts, ensure_ascii=True) + "\n")
