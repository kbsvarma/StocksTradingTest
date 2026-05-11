"""Structured logger for the Webull bot — console + file + JSONL trade log."""
from __future__ import annotations

import csv
import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from typing import Any


class BotLogger:
    def __init__(self, logs_dir: str, trade_csv: str):
        self.logs_dir = Path(logs_dir)
        self.logs_dir.mkdir(parents=True, exist_ok=True)

        self.trade_csv_path = Path(trade_csv)
        self.trade_csv_path.parent.mkdir(parents=True, exist_ok=True)

        self.order_events_path = self.logs_dir / "order_events.jsonl"
        self.signal_events_path = self.logs_dir / "signal_events.jsonl"

        self._lock = Lock()

        self.log = logging.getLogger("webull_bot")
        self.log.setLevel(logging.INFO)
        self.log.handlers.clear()

        fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s", datefmt="%Y-%m-%dT%H:%M:%S")

        sh = logging.StreamHandler()
        sh.setFormatter(fmt)
        self.log.addHandler(sh)

        fh = logging.FileHandler(self.logs_dir / "system.log", encoding="utf-8")
        fh.setFormatter(fmt)
        self.log.addHandler(fh)

    def info(self, msg: str) -> None:
        self.log.info(msg)

    def warning(self, msg: str) -> None:
        self.log.warning(msg)

    def error(self, msg: str) -> None:
        self.log.error(msg)

    def signal_event(self, event_type: str, payload: dict[str, Any]) -> None:
        self._append_jsonl(self.signal_events_path, {"event": event_type, **payload})

    def order_event(self, event_type: str, payload: dict[str, Any]) -> None:
        self._append_jsonl(self.order_events_path, {"event": event_type, **payload})

    def append_trade(
        self,
        date: str,
        symbol: str,
        expiry: str,
        short_strike: float,
        long_strike: float,
        entry_credit: float,
        entry_spx: float,
        entry_vix: float,
        exit_price: float,
        pnl_pts: float,
        pnl_usd: float,
        exit_reason: str,
        notes: str = "",
    ) -> None:
        row = {
            "Date": date,
            "Symbol": symbol,
            "Expiry": expiry,
            "Short Strike": short_strike,
            "Long Strike": long_strike,
            "Entry Credit": entry_credit,
            "SPX at Entry": entry_spx,
            "VIX at Entry": entry_vix,
            "Exit Price": exit_price,
            "PnL pts": pnl_pts,
            "PnL USD": pnl_usd,
            "Exit Reason": exit_reason,
            "Notes": notes,
        }
        fieldnames = list(row.keys())
        with self._lock:
            write_header = not self.trade_csv_path.exists() or self.trade_csv_path.stat().st_size == 0
            with self.trade_csv_path.open("a", newline="", encoding="utf-8") as fh:
                writer = csv.DictWriter(fh, fieldnames=fieldnames)
                if write_header:
                    writer.writeheader()
                writer.writerow(row)

    def _append_jsonl(self, path: Path, payload: dict[str, Any]) -> None:
        payload_ts = {"ts": datetime.now(UTC).isoformat(), **payload}
        with self._lock:
            with path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(payload_ts) + "\n")
