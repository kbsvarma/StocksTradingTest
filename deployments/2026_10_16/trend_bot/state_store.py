"""Atomic JSON state + append-only CSV ledgers.

state.json          — human-readable position/rotation projection
trades.csv          — legacy human-readable trade projection
equity.csv          — hourly mark-to-market per book (core / sleeve)
decisions.csv       — every signal evaluation (gate states) for reconcile
events.log          — human-readable event stream (mode toggles, rolls, errors)
"""
from __future__ import annotations

import csv
import copy
import hashlib
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger("trend_bot.state")


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


EMPTY_POS = {
    "position": 0, "qty": 0, "entry_px": None, "entry_time": None,
    "hw": None, "stop_px": None, "sig_atr": None, "con_id": None,
    "local_symbol": None, "stop_order_id": None, "entry_weight": None,
    "entry_commission": None, "entry_exec_ids": [],
    "entry_fees_allocated": False, "basis_quality": None,
    "basis_qty": None, "roll_event_key": None,
}


class StateStore:
    def __init__(self, state_dir: Path):
        self.dir = Path(state_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.path = self.dir / "state.json"
        self.load_error: str | None = None
        self.quarantined_path: Path | None = None
        self.state = self._load()

    @staticmethod
    def _empty_state() -> dict:
        return {
            "created": utcnow(),
            "paper_start": None,
            "soak_start": None,       # explicit durable qualification epoch
            "soak_release_valid": None,
            "positions": {},          # sym -> EMPTY_POS-shaped dict
            "rotation": None,         # last weights result + applied date
            "mode": None,             # config default until toggled
            "mode_history": [],
            "halt_entries": False,
            "halt_reason": None,
            "last_wake": None,
            "last_bar": {},
        }

    def _load(self) -> dict:
        if self.path.exists():
            try:
                state = json.loads(self.path.read_text())
                if not isinstance(state, dict):
                    raise ValueError("state.json root is not an object")
                for key in ("positions", "last_bar", "order_intents",
                            "pending_entries", "last_decision_bar"):
                    if key in state and not isinstance(state[key], dict):
                        raise ValueError(f"state.json {key} is not an object")
                if ("runtime_identity" in state and
                        not isinstance(state["runtime_identity"], dict)):
                    raise ValueError(
                        "state.json runtime_identity is not an object")
                if ("mode_history" in state and
                        not isinstance(state["mode_history"], list)):
                    raise ValueError("state.json mode_history is not a list")
                return {**self._empty_state(), **state}
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
                quarantine = self.path.with_name(
                    f"state.json.corrupt.{stamp}.{os.getpid()}")
                try:
                    self.path.replace(quarantine)
                    self.quarantined_path = quarantine
                except OSError:
                    quarantine = None
                self.load_error = f"{type(exc).__name__}: {exc}"
                log.error("state projection unreadable; quarantined=%s: %s",
                          quarantine, self.load_error)
        return self._empty_state()

    def save(self):
        self.state["last_saved"] = utcnow()
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.state, indent=2, default=str))
        tmp.replace(self.path)

    def bind_identity(self, venue: str, account: str, *,
                      allow_legacy_paper: bool = True,
                      generation: str | None = None) -> str:
        """Bind this projection to one immutable venue/account generation.

        Existing paper state is migrated in place once.  Existing unbound
        state is never allowed to become live state: a venue switch must start
        from a newly archived/empty state directory.
        """
        if not account:
            raise RuntimeError("cannot bind state without a broker account")
        ident = self.state.get("runtime_identity")
        if ident:
            if ident.get("venue") != venue or ident.get("account") != account:
                raise RuntimeError(
                    "STATE IDENTITY MISMATCH: state.json belongs to "
                    f"{ident.get('venue')}/{ident.get('account')}, connected "
                    f"to {venue}/{account}; refusing to trade")
            return ident["generation"]
        has_history = bool(self.state.get("paper_start")) or any(
            p.get("position") for p in self.state.get("positions", {}).values())
        if venue != "ibkr_paper" and has_history:
            raise RuntimeError(
                "REFUSING live venue with legacy/unbound state; archive state/ "
                "and start a fresh live generation")
        if venue == "ibkr_paper" and has_history and not allow_legacy_paper:
            raise RuntimeError("legacy paper-state migration disabled")
        now = utcnow()
        generation = generation or hashlib.sha256(
            f"{venue}:{account}:{now}".encode()).hexdigest()[:16]
        self.state["runtime_identity"] = {
            "venue": venue, "account": account,
            "generation": generation, "bound_at": now,
        }
        self.save()
        return generation

    def hydrate_positions(self, positions: dict[str, dict]):
        """Replace JSON position projection from durable FSM truth."""
        for sym, position in positions.items():
            clean = dict(position)
            clean.pop("fsm_state", None)
            self.state["positions"][sym] = {**copy.deepcopy(EMPTY_POS), **clean}
        self.save()

    def pos(self, sym: str) -> dict:
        return self.state["positions"].setdefault(sym, copy.deepcopy(EMPTY_POS))

    def flatten(self, sym: str):
        self.state["positions"][sym] = copy.deepcopy(EMPTY_POS)

    # ---- ledgers ------------------------------------------------------
    def _append_csv(self, name: str, row: dict, header: list[str]):
        p = self.dir / name
        new = not p.exists()
        with p.open("a", newline="") as f:
            w = csv.DictWriter(f, fieldnames=header)
            if new:
                w.writeheader()
            w.writerow(row)

    TRADE_COLS = ["exit_time", "book", "sym", "trade_symbol", "qty", "entry_time",
                  "entry_px", "exit_px", "reason", "pnl_points", "pnl_usd",
                  "commission", "entry_weight", "local_symbol"]

    def record_trade(self, **kw):
        self._append_csv("trades.csv", {c: kw.get(c) for c in self.TRADE_COLS},
                         self.TRADE_COLS)
        self.event(f"TRADE {kw.get('sym')} qty={kw.get('qty')} "
                   f"{kw.get('entry_px')}->{kw.get('exit_px')} "
                   f"({kw.get('reason')}) pnl=${kw.get('pnl_usd'):.2f}")

    EQUITY_COLS = ["ts", "book", "open_pnl_usd", "closed_pnl_usd", "total_usd"]

    def record_equity(self, **kw):
        self._append_csv("equity.csv", {c: kw.get(c) for c in self.EQUITY_COLS},
                         self.EQUITY_COLS)

    DECISION_COLS = ["ts", "sym", "bar", "signal", "acted", "breakout", "vol_ok",
                     "session_ok", "close", "donchian", "atr", "atr_pct",
                     "vol_thresh", "weight", "note", "vix", "vix_regime", "oi_chg5", "oi_state", "vol_ratio"]

    def record_decision(self, **kw):
        self._append_csv("decisions.csv", {c: kw.get(c) for c in self.DECISION_COLS},
                         self.DECISION_COLS)

    def event(self, msg: str):
        line = f"{utcnow()} {msg}"
        with (self.dir / "events.log").open("a") as f:
            f.write(line + "\n")
        log.info(msg)

    def heartbeat(self):
        (self.dir / "heartbeat").write_text(utcnow())
