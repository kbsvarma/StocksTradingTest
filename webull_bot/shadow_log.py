"""Shadow logging — record what Phase 2.3 (5× 10pt × 0.75%) would have done
at the same moment the live bot enters/exits a Phase 1 trade.

Design (user's idea 2026-05-21):
  - When live bot records a fill → query IBKR for what the Phase 2.3 spread
    would price at right now, log the virtual entry.
  - When live bot's monitor exits → query IBKR for the current mark of that
    virtual spread, compute virtual P&L, log the exit.
  - Output: spx_spread_bot/data/shadow_phase23/trades.csv

Safety invariants (CRITICAL):
  - This module NEVER raises to the caller. All errors caught + logged.
  - Adds exactly TWO IBKR calls per live trade lifecycle (1 entry, 1 exit).
  - Append-only. Never touches state.json, live trades.csv, or anything live.
  - If IBKR is unreachable, logs a placeholder row + moves on.

Slippage modeling (per user spec):
  - Entry credit = IBKR mid - limit_price_offset (matches what live bot would have done)
  - Exit mark = IBKR mid at exit moment (no extra slippage modeled — Option 1 minimal)
  - On SL exits, the exit mark is sampled at LIVE EXIT TIME, not at the shadow's
    own stop trigger (would require parallel monitor — see Option 2 for that).
    Logged with `shadow_data_quality=approx_on_sl` flag.

Config (hard-coded — matches config_phase23_paper.yaml):
"""
from __future__ import annotations

import csv
import json
import os
import traceback
from datetime import datetime
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")

# Phase 2.3 candidate parameters — matches config_phase23_paper.yaml
SHADOW_OTM_PCT       = 0.0075
SHADOW_SPREAD_WIDTH  = 10
SHADOW_QUANTITY      = 5
SHADOW_LIMIT_OFFSET  = 0.10
SHADOW_DATA_DIR      = "spx_spread_bot/data/shadow_phase23"

CSV_COLUMNS = [
    "entry_ts",
    "expiry",
    "spx_at_entry",
    "vix_at_entry",
    # Live trade context (for paired analysis)
    "live_short", "live_long", "live_credit", "live_exit_reason", "live_pnl_usd",
    # Shadow Phase 2.3 entry
    "shadow_short", "shadow_long",
    "shadow_mid_at_entry", "shadow_limit_at_entry", "shadow_credit_total",
    # Shadow exit
    "exit_ts", "shadow_exit_mark", "shadow_pnl_usd",
    # Quality flag — "accurate" for EOD, "approx_on_sl" for SL exits
    "shadow_data_quality",
    # Error info if anything failed
    "notes",
]


def _open_pos_path() -> Path:
    p = Path(SHADOW_DATA_DIR) / "open.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _trades_csv_path() -> Path:
    p = Path(SHADOW_DATA_DIR) / "trades.csv"
    p.parent.mkdir(parents=True, exist_ok=True)
    if not p.exists():
        with p.open("w", newline="") as f:
            csv.DictWriter(f, fieldnames=CSV_COLUMNS).writeheader()
    return p


def _safe_log(msg: str) -> None:
    """Print to stderr — visible in launchd logs but won't break anything."""
    ts = datetime.now(ET).strftime("%H:%M:%S")
    print(f"[shadow-log {ts}] {msg}", flush=True)


def log_entry(
    spx_at_entry: float,
    vix_at_entry: float,
    expiry: str,
    yf_options_symbol: str,
    live_short: float,
    live_long: float,
    live_credit: float,
) -> None:
    """Called right after live bot records a fill.

    Queries IBKR for the Phase 2.3 spread at this moment, writes the
    virtual-entry record to open.json.

    NEVER raises — all errors caught and logged.
    """
    try:
        from webull_bot.market_data import find_best_spread

        shadow_spread = find_best_spread(
            spx_price=spx_at_entry,
            otm_pct=SHADOW_OTM_PCT,
            spread_width=SHADOW_SPREAD_WIDTH,
            min_credit=0.05,  # accept anything reasonable — we're shadowing, not gating
            yf_options_symbol=yf_options_symbol,
            expiry=expiry,
        )
        if shadow_spread is None:
            _safe_log(f"no shadow spread found at SPX={spx_at_entry}; logging placeholder")
            shadow_short = shadow_long = 0.0
            shadow_mid = 0.0
            shadow_limit = 0.0
            note = "no shadow spread found (find_best_spread returned None)"
        else:
            shadow_short = float(shadow_spread.short_strike)
            shadow_long = float(shadow_spread.long_strike)
            shadow_mid = float(shadow_spread.mid)
            # Match live bot's pricing: limit = mid - offset, tick-aligned
            shadow_limit = round(shadow_mid - SHADOW_LIMIT_OFFSET, 2)
            note = ""

        shadow_credit_total = shadow_limit * SHADOW_QUANTITY * 100  # USD

        open_pos = {
            "entry_ts": datetime.now(ET).isoformat(),
            "expiry": expiry,
            "spx_at_entry": spx_at_entry,
            "vix_at_entry": vix_at_entry,
            "live_short": live_short,
            "live_long": live_long,
            "live_credit": live_credit,
            "shadow_short": shadow_short,
            "shadow_long": shadow_long,
            "shadow_mid_at_entry": shadow_mid,
            "shadow_limit_at_entry": shadow_limit,
            "shadow_credit_total": shadow_credit_total,
            "entry_note": note,
        }
        _open_pos_path().write_text(json.dumps(open_pos, indent=2))
        _safe_log(
            f"ENTRY  live={int(live_short)}/{int(live_long)}@${live_credit:.2f}  "
            f"shadow={int(shadow_short)}/{int(shadow_long)}@${shadow_limit:.2f}×{SHADOW_QUANTITY}="
            f"${shadow_credit_total:.0f}"
        )
    except Exception:
        # Critical: must NEVER raise. Just log and move on.
        _safe_log(f"ENTRY LOG FAILED:\n{traceback.format_exc()}")


def log_exit(
    live_exit_reason: str,
    live_pnl_usd: float,
) -> None:
    """Called right after live bot's monitor.run_until_closed() returns.

    Queries IBKR for the shadow spread's current mark, computes virtual P&L,
    writes completed shadow trade to trades.csv. Clears open.json.

    NEVER raises — all errors caught and logged.
    """
    try:
        open_path = _open_pos_path()
        if not open_path.exists():
            _safe_log("EXIT called but no open shadow position found — skipping")
            return
        try:
            open_pos = json.loads(open_path.read_text())
        except Exception:
            _safe_log("EXIT failed to parse open.json — skipping")
            return

        # Determine data quality flag
        if live_exit_reason and "STOP" in live_exit_reason.upper():
            quality = "approx_on_sl"
        else:
            quality = "accurate"

        # Query shadow exit mark
        from webull_bot.ibkr_market_data import get_spread_mark_ibkr

        shadow_short = open_pos.get("shadow_short", 0)
        shadow_long = open_pos.get("shadow_long", 0)
        expiry = open_pos.get("expiry", "")
        exit_note = ""

        if shadow_short == 0 or shadow_long == 0:
            shadow_exit_mark = 0.0
            shadow_pnl_usd = 0.0
            exit_note = "skipped (no shadow entry recorded)"
        else:
            try:
                mark = get_spread_mark_ibkr(
                    short_strike=shadow_short,
                    long_strike=shadow_long,
                    expiry=expiry,
                )
                if mark is None:
                    shadow_exit_mark = 0.0
                    shadow_pnl_usd = 0.0
                    exit_note = "IBKR returned None for exit mark — using 0"
                else:
                    shadow_exit_mark = float(mark)
                    # Per-spread: limit_received - exit_paid. ×qty ×100 = USD
                    per_spread_pnl_pts = open_pos["shadow_limit_at_entry"] - shadow_exit_mark
                    shadow_pnl_usd = round(per_spread_pnl_pts * SHADOW_QUANTITY * 100, 2)
            except Exception as exc:
                shadow_exit_mark = 0.0
                shadow_pnl_usd = 0.0
                exit_note = f"IBKR call failed: {exc}"

        row = {
            "entry_ts":             open_pos.get("entry_ts", ""),
            "expiry":               expiry,
            "spx_at_entry":         open_pos.get("spx_at_entry", 0),
            "vix_at_entry":         open_pos.get("vix_at_entry", 0),
            "live_short":           open_pos.get("live_short", 0),
            "live_long":            open_pos.get("live_long", 0),
            "live_credit":          open_pos.get("live_credit", 0),
            "live_exit_reason":     live_exit_reason or "",
            "live_pnl_usd":         live_pnl_usd or 0,
            "shadow_short":         shadow_short,
            "shadow_long":          shadow_long,
            "shadow_mid_at_entry":  open_pos.get("shadow_mid_at_entry", 0),
            "shadow_limit_at_entry": open_pos.get("shadow_limit_at_entry", 0),
            "shadow_credit_total":  open_pos.get("shadow_credit_total", 0),
            "exit_ts":              datetime.now(ET).isoformat(),
            "shadow_exit_mark":     shadow_exit_mark,
            "shadow_pnl_usd":       shadow_pnl_usd,
            "shadow_data_quality":  quality,
            "notes":                f"{open_pos.get('entry_note','')} | {exit_note}".strip(" |"),
        }
        with _trades_csv_path().open("a", newline="") as f:
            csv.DictWriter(f, fieldnames=CSV_COLUMNS).writerow(row)
        _safe_log(
            f"EXIT   live={live_exit_reason} pnl=${live_pnl_usd:.0f}  "
            f"shadow_exit=${shadow_exit_mark:.2f}  shadow_pnl=${shadow_pnl_usd:.0f}  "
            f"quality={quality}"
        )
        # Clear open position
        try:
            open_path.unlink()
        except Exception:
            pass
    except Exception:
        _safe_log(f"EXIT LOG FAILED:\n{traceback.format_exc()}")
