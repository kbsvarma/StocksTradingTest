"""Tier 1 proposals — schema, hard-rail validation, lifecycle, Telegram format.

A proposal is one JSON file in advisor/data/proposals/P_<ID>.json. Lifecycle:

    PENDING → APPROVED → EXECUTING → EXECUTED
            ↘ REJECTED            ↘ FAILED
            ↘ EXPIRED

Hard rails are validated HERE at creation time and re-checked by the
executor at execution time (advisor/config_advisor.yaml limits block).
Rails live in code, never in prompts.

CLI:
    python -m advisor.proposals --new --symbol SPXW --expiry 2026-06-12 \
        --short 7500 --long 7450 --qty 1 --limit 1.90 \
        --conviction high --rationale "..." [--ttl 120] [--no-telegram]
    python -m advisor.proposals --list
    python -m advisor.proposals --show ID
    python -m advisor.proposals --approve-local ID    # test/emergency only —
        # normal approval path is the Telegram reply listener
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

ET = ZoneInfo("America/New_York")
REPO_ROOT = Path(__file__).resolve().parent.parent

VALID_STATUS = {"PENDING", "APPROVED", "REJECTED", "EXPIRED",
                "EXECUTING", "EXECUTED", "FAILED"}
# Legal transitions — anything else is refused (idempotency guard)
TRANSITIONS = {
    ("PENDING", "APPROVED"), ("PENDING", "REJECTED"), ("PENDING", "EXPIRED"),
    ("APPROVED", "EXECUTING"), ("APPROVED", "EXPIRED"),
    ("EXECUTING", "EXECUTED"), ("EXECUTING", "FAILED"),
}


def load_advisor_cfg() -> dict:
    return yaml.safe_load((REPO_ROOT / "advisor" / "config_advisor.yaml").read_text())


def proposals_dir(cfg: dict | None = None) -> Path:
    env = os.environ.get("ADVISOR_DATA_DIR")
    if env:
        d = Path(env) / "proposals"
    else:
        cfg = cfg or load_advisor_cfg()
        d = REPO_ROOT / cfg["paths"]["proposals_dir"]
    d.mkdir(parents=True, exist_ok=True)
    return d


@dataclass
class Proposal:
    id: str
    created_ts: str
    expires_ts: str
    kind: str                 # "BPS" — bull put spread (only kind in v1)
    symbol: str
    expiry: str               # YYYY-MM-DD
    short_strike: float
    long_strike: float
    qty: int
    limit_price: float        # net credit limit, tick-aligned
    max_loss_usd: float
    rationale: str
    conviction: str           # high | medium
    status: str = "PENDING"
    fill_price: float | None = None
    executed_ts: str | None = None
    history: list = field(default_factory=list)

    def path(self) -> Path:
        return proposals_dir() / f"P_{self.id}.json"

    def expired(self, now: datetime | None = None) -> bool:
        now = now or datetime.now(ET)
        return now > datetime.fromisoformat(self.expires_ts)


def _now() -> datetime:
    return datetime.now(ET)


def _save(p: Proposal) -> None:
    """Atomic write (tmp + os.replace) — same crash-safety as StateStore."""
    path = p.path()
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(asdict(p), indent=2))
    os.replace(tmp, path)


def load(pid: str) -> Proposal | None:
    path = proposals_dir() / f"P_{pid.upper()}.json"
    if not path.exists():
        return None
    raw = json.loads(path.read_text())
    return Proposal(**{k: v for k, v in raw.items()
                       if k in Proposal.__dataclass_fields__})


def load_all() -> list[Proposal]:
    out = []
    for f in sorted(proposals_dir().glob("P_*.json")):
        try:
            raw = json.loads(f.read_text())
            out.append(Proposal(**{k: v for k, v in raw.items()
                                   if k in Proposal.__dataclass_fields__}))
        except Exception:
            continue
    return out


def created_today(statuses: set[str] | None = None) -> list[Proposal]:
    today = _now().date().isoformat()
    return [p for p in load_all()
            if p.created_ts[:10] == today
            and (statuses is None or p.status in statuses)]


def transition(pid: str, new_status: str, detail: str = "") -> Proposal:
    """Validated, history-logged state transition. Raises on illegal moves."""
    p = load(pid)
    if p is None:
        raise ValueError(f"unknown proposal {pid}")
    if (p.status, new_status) not in TRANSITIONS:
        raise ValueError(f"illegal transition {p.status} → {new_status} for {pid}")
    p.history.append({"ts": _now().isoformat(), "from": p.status,
                      "to": new_status, "detail": detail})
    p.status = new_status
    _save(p)
    return p


def tick_aligned(price: float, tick: float) -> bool:
    return abs(round(price / tick) * tick - price) < 1e-9


def validate(symbol: str, expiry: str, short: float, long_: float,
             qty: int, limit_price: float, ttl_min: int,
             cfg: dict | None = None) -> list[str]:
    """Return list of hard-rail violations (empty = valid)."""
    cfg = cfg or load_advisor_cfg()
    lim = cfg["limits"]
    errs: list[str] = []
    if symbol not in lim["symbol_whitelist"]:
        errs.append(f"symbol {symbol} not in whitelist {lim['symbol_whitelist']}")
    try:
        if date.fromisoformat(expiry) < _now().date():
            errs.append(f"expiry {expiry} is in the past")
    except ValueError:
        errs.append(f"expiry {expiry!r} not YYYY-MM-DD")
    if short <= long_:
        errs.append(f"short strike {short} must be > long strike {long_} (bull put spread)")
    width = short - long_
    if width > lim["max_width_points"]:
        errs.append(f"width {width}pt exceeds max {lim['max_width_points']}pt")
    if not 1 <= qty <= lim["max_qty"]:
        errs.append(f"qty {qty} outside 1..{lim['max_qty']}")
    if limit_price <= 0:
        errs.append("limit_price must be positive")
    if not tick_aligned(limit_price, lim["tick_size"]):
        errs.append(f"limit {limit_price} not aligned to ${lim['tick_size']} tick")
    max_loss = (width * 100 - limit_price * 100) * qty
    if max_loss > lim["max_loss_per_trade_usd"]:
        errs.append(f"max loss ${max_loss:.0f} exceeds cap ${lim['max_loss_per_trade_usd']}")
    if ttl_min > cfg["approval"]["ttl_minutes_max"]:
        errs.append(f"ttl {ttl_min}min exceeds max {cfg['approval']['ttl_minutes_max']}min")
    n_today = len(created_today())
    if n_today >= lim["max_proposals_per_day"]:
        errs.append(f"daily proposal cap reached ({n_today}/{lim['max_proposals_per_day']})")
    return errs


def new_proposal(symbol: str, expiry: str, short: float, long_: float,
                 qty: int, limit_price: float, rationale: str,
                 conviction: str, ttl_min: int | None = None) -> Proposal:
    cfg = load_advisor_cfg()
    ttl = ttl_min or cfg["approval"]["ttl_minutes_default"]
    errs = validate(symbol, expiry, short, long_, qty, limit_price, ttl, cfg)
    if errs:
        raise ValueError("hard-rail violation(s): " + "; ".join(errs))
    now = _now()
    p = Proposal(
        id=uuid.uuid4().hex[:4].upper(),
        created_ts=now.isoformat(),
        expires_ts=(now + timedelta(minutes=ttl)).isoformat(),
        kind="BPS", symbol=symbol, expiry=expiry,
        short_strike=float(short), long_strike=float(long_), qty=int(qty),
        limit_price=float(limit_price),
        max_loss_usd=round((short - long_) * 100 * qty - limit_price * 100 * qty, 2),
        rationale=rationale.strip(), conviction=conviction,
        history=[{"ts": now.isoformat(), "from": None, "to": "PENDING",
                  "detail": "created"}],
    )
    _save(p)
    return p


def telegram_message(p: Proposal) -> str:
    exp_t = datetime.fromisoformat(p.expires_ts).strftime("%H:%M ET")
    return (
        f"📨 TRADE PROPOSAL  [{p.id}]\n"
        f"{p.symbol} {p.expiry}  {int(p.short_strike)}/{int(p.long_strike)}P  "
        f"qty={p.qty}\n"
        f"LIMIT credit ≥ ${p.limit_price:.2f}   max loss ${p.max_loss_usd:.0f}\n"
        f"conviction: {p.conviction.upper()}\n"
        f"---\n{p.rationale}\n---\n"
        f"Reply  YES {p.id}  to approve and execute\n"
        f"Reply  NO {p.id}   to reject\n"
        f"⏳ expires {exp_t}"
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--new", action="store_true")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--show")
    ap.add_argument("--approve-local")
    ap.add_argument("--symbol"); ap.add_argument("--expiry")
    ap.add_argument("--short", type=float); ap.add_argument("--long", type=float)
    ap.add_argument("--qty", type=int, default=1)
    ap.add_argument("--limit", type=float)
    ap.add_argument("--rationale", default="")
    ap.add_argument("--conviction", choices=["high", "medium"], default="medium")
    ap.add_argument("--ttl", type=int, default=None)
    ap.add_argument("--no-telegram", action="store_true")
    a = ap.parse_args()

    if a.list:
        for p in load_all():
            print(f"{p.id}  {p.status:<9} {p.symbol} {p.expiry} "
                  f"{int(p.short_strike)}/{int(p.long_strike)}P qty={p.qty} "
                  f"limit={p.limit_price}  created={p.created_ts[:16]}")
        return 0
    if a.show:
        p = load(a.show)
        print(json.dumps(asdict(p), indent=2) if p else f"unknown proposal {a.show}")
        return 0 if p else 1
    if a.approve_local:
        p = transition(a.approve_local, "APPROVED", "approve-local (test/emergency CLI)")
        print(f"{p.id} → APPROVED (local). Execute: python -m advisor.executor --proposal {p.id}")
        return 0
    if a.new:
        if not all([a.symbol, a.expiry, a.short, a.long, a.limit]):
            ap.error("--new requires --symbol --expiry --short --long --limit")
        try:
            p = new_proposal(a.symbol, a.expiry, a.short, a.long, a.qty,
                             a.limit, a.rationale, a.conviction, a.ttl)
        except ValueError as exc:
            print(f"REFUSED: {exc}", file=sys.stderr)
            return 1
        print(f"created {p.id}  expires {p.expires_ts}")
        if not a.no_telegram:
            from advisor import telegram_io
            telegram_io.send(telegram_message(p))
            print("proposal sent to Telegram")
        return 0
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
