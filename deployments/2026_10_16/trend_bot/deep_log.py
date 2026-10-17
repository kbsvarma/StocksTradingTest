"""Deep forensic logging — append-only JSONL, one file per stream per UTC day.

Streams (state/logs/{stream}-YYYYMMDD.jsonl):
  orders       every order lifecycle event: submit, ack, status change, fill,
               cancel — with raw venue payloads (orderId/permId/status/filled/
               avgFillPrice/commission/orderRef) and monotonic latencies
  stops        every stop modification: old→new, high-water, ATR, k, tick,
               who moved it (trail/rebase/re-arm) and venue ack
  decisions    every signal evaluation with full gate context (mirrors
               decisions.csv but with the complete indicator dict)
  bars         every bar fetch: venue, symbol, duration requested, rows
               returned, first/last ts, request latency ms, rows appended
  wakes        every wake/subpoll: scheduled vs actual time (drift), per-
               instrument processing ms, totals
  rotation     every rebalance: full inputs (per-index first/last close +
               dates used for the 21d return), outputs, mode, spread
  connections  every connect/disconnect/reconnect per client with reason
  account      startup reconciliation snapshots: venue positions vs state

Rationale: 4+ weeks of paper must be forensically reconstructible — the BPS
engine was validated fill-by-fill against exactly this kind of record.
"""
from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path

_lock = threading.Lock()


class DeepLog:
    def __init__(self, log_dir: Path):
        self.dir = Path(log_dir)
        self.dir.mkdir(parents=True, exist_ok=True)

    def log(self, stream: str, **kw):
        """Never raises — forensic logging must not break trading."""
        try:
            now = datetime.now(timezone.utc)
            row = {"ts": now.isoformat(timespec="milliseconds")}
            row.update(kw)
            p = self.dir / f"{stream}-{now:%Y%m%d}.jsonl"
            line = json.dumps(row, default=str, separators=(",", ":"))
            with _lock, p.open("a") as f:
                f.write(line + "\n")
        except Exception:
            pass


_NULL = None


def get_logger(log_dir: Path | None = None) -> DeepLog:
    global _NULL
    if log_dir is not None:
        return DeepLog(log_dir)
    if _NULL is None:
        class _Noop(DeepLog):
            def __init__(self):
                pass

            def log(self, stream, **kw):
                pass
        _NULL = _Noop()
    return _NULL
