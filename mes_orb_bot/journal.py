# journal.py — Structured trade + status writer for the dashboard.
#
# Two outputs, both in logs/:
#   status.json          — overwritten every bar (live state for the dashboard)
#   trades_YYYY-MM-DD.jsonl — one JSON line appended per completed trade
#   summary_YYYY-MM-DD.json — written at EOD (one object, full session report)

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

import config


_TZ = ZoneInfo(config.TIMEZONE)


def _log_dir() -> Path:
    p = Path(config.LOG_DIR)
    p.mkdir(exist_ok=True)
    return p


def _today() -> str:
    return datetime.now(_TZ).strftime("%Y-%m-%d")


def _now_iso() -> str:
    return datetime.now(_TZ).isoformat(timespec="seconds")


def _write_atomic(path: Path, obj: Dict[str, Any]) -> None:
    """Write JSON atomically (temp file → rename) to avoid partial reads."""
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, indent=2, default=str))
    tmp.replace(path)


# ── Status (live) ──────────────────────────────────────────────────────────────

def write_status(
    *,
    state: str,
    range_high: Optional[float] = None,
    range_low: Optional[float] = None,
    range_width: Optional[float] = None,
    vwap: Optional[float] = None,
    direction: Optional[str] = None,
    entry_price: Optional[float] = None,
    stop_price: Optional[float] = None,
    target_price: Optional[float] = None,
    bars_in_trade: int = 0,
    unrealized_pnl_pts: Optional[float] = None,
    unrealized_pnl_usd: Optional[float] = None,
    daily_gross_usd: float = 0.0,
    daily_comm_usd: float = 0.0,
    daily_net_usd: float = 0.0,
    daily_trades: int = 0,
    daily_wins: int = 0,
    vix: Optional[float] = None,
    halt_reason: Optional[str] = None,
) -> None:
    status = {
        "timestamp": _now_iso(),
        "session_date": _today(),
        "state": state,
        # Opening range
        "range_high": range_high,
        "range_low": range_low,
        "range_width": range_width,
        # Live position
        "vwap": vwap,
        "direction": direction,
        "entry_price": entry_price,
        "stop_price": stop_price,
        "target_price": target_price,
        "bars_in_trade": bars_in_trade,
        "unrealized_pnl_pts": unrealized_pnl_pts,
        "unrealized_pnl_usd": unrealized_pnl_usd,
        # Daily running totals
        "daily_gross_usd": round(daily_gross_usd, 2),
        "daily_comm_usd": round(daily_comm_usd, 2),
        "daily_net_usd": round(daily_net_usd, 2),
        "daily_trades": daily_trades,
        "daily_wins": daily_wins,
        # Context
        "vix": vix,
        "halt_reason": halt_reason,
        "contracts": config.CONTRACTS,
        "symbol": config.SYMBOL,
        "paper_trading": config.PAPER_TRADING,
    }
    _write_atomic(_log_dir() / "status.json", status)


# ── Trade record ───────────────────────────────────────────────────────────────

def append_trade(
    *,
    entry_time: datetime,
    exit_time: datetime,
    direction: str,
    entry_price: float,
    exit_price: float,
    exit_reason: str,
    pnl_pts: float,
    gross_usd: float,
    commission: float,
    net_usd: float,
    range_high: Optional[float],
    range_low: Optional[float],
    range_width: Optional[float],
    vix_at_entry: Optional[float],
    gap_pct: Optional[float],
) -> None:
    hold_min = (exit_time - entry_time).total_seconds() / 60.0
    record = {
        "date": _today(),
        "entry_time": entry_time.strftime("%H:%M:%S"),
        "exit_time": exit_time.strftime("%H:%M:%S"),
        "direction": direction,
        "entry_price": entry_price,
        "exit_price": exit_price,
        "exit_reason": exit_reason,
        "pnl_pts": round(pnl_pts, 4),
        "gross_usd": round(gross_usd, 2),
        "commission": round(commission, 2),
        "net_usd": round(net_usd, 2),
        "hold_min": round(hold_min, 1),
        "range_high": range_high,
        "range_low": range_low,
        "range_width": range_width,
        "vix_at_entry": vix_at_entry,
        "gap_pct": gap_pct,
        "contracts": config.CONTRACTS,
        "outcome": "WIN" if pnl_pts > 0 else "LOSS",
    }
    path = _log_dir() / f"trades_{_today()}.jsonl"
    with open(path, "a") as f:
        f.write(json.dumps(record) + "\n")


# ── EOD summary ────────────────────────────────────────────────────────────────

def write_summary(
    *,
    trades: List[Dict[str, Any]],
    final_state: str,
    halt_reason: Optional[str],
    vix: Optional[float],
    prev_close: Optional[float],
    today_open: Optional[float],
    atr: Optional[float],
    range_high: Optional[float],
    range_low: Optional[float],
    range_width: Optional[float],
    session_vwap: Optional[float],
    signal_evals: int,
) -> None:
    n = len(trades)
    wins = [t for t in trades if t["outcome"] == "WIN"]
    losses = [t for t in trades if t["outcome"] == "LOSS"]
    total_net = sum(t["net_usd"] for t in trades)
    total_gross = sum(t["gross_usd"] for t in trades)
    total_comm = sum(t["commission"] for t in trades)

    by_reason: Dict[str, int] = {}
    for t in trades:
        by_reason[t["exit_reason"]] = by_reason.get(t["exit_reason"], 0) + 1

    summary = {
        "date": _today(),
        "generated_at": _now_iso(),
        # Trade stats
        "total_trades": n,
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(len(wins) / n, 4) if n else None,
        "avg_win_usd": round(sum(t["net_usd"] for t in wins) / len(wins), 2) if wins else None,
        "avg_loss_usd": round(sum(t["net_usd"] for t in losses) / len(losses), 2) if losses else None,
        # P&L
        "total_gross_usd": round(total_gross, 2),
        "total_comm_usd": round(total_comm, 2),
        "total_net_usd": round(total_net, 2),
        # Exit breakdown
        "exits_by_reason": by_reason,
        # Session context
        "final_state": final_state,
        "halt_reason": halt_reason,
        "vix": vix,
        "prev_close": prev_close,
        "today_open": today_open,
        "atr_20d": atr,
        "range_high": range_high,
        "range_low": range_low,
        "range_width": range_width,
        "session_vwap": session_vwap,
        "signal_evals": signal_evals,
        "symbol": config.SYMBOL,
        "contracts": config.CONTRACTS,
        "paper_trading": config.PAPER_TRADING,
        "trades": trades,
    }
    _write_atomic(_log_dir() / f"summary_{_today()}.json", summary)


# ── Historical loader (used by dashboard) ─────────────────────────────────────

def load_all_summaries(log_dir: str = None) -> List[Dict[str, Any]]:
    """Load all summary_*.json files, newest first."""
    d = Path(log_dir or config.LOG_DIR)
    rows = []
    for f in sorted(d.glob("summary_*.json"), reverse=True):
        try:
            rows.append(json.loads(f.read_text()))
        except Exception:
            pass
    return rows


def load_today_trades(log_dir: str = None) -> List[Dict[str, Any]]:
    """Load today's trades_YYYY-MM-DD.jsonl."""
    d = Path(log_dir or config.LOG_DIR)
    path = d / f"trades_{_today()}.jsonl"
    if not path.exists():
        return []
    rows = []
    for line in path.read_text().splitlines():
        try:
            rows.append(json.loads(line))
        except Exception:
            pass
    return rows


def load_status(log_dir: str = None) -> Optional[Dict[str, Any]]:
    """Load current status.json, or None if missing/stale."""
    d = Path(log_dir or config.LOG_DIR)
    path = d / "status.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except Exception:
        return None
