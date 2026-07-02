"""Evening librarian queue — deterministic pick of 3-5 dossier targets.

Priority: open-call names (refresh) → factor-sheet new entrants (new) →
top longs/shorts lacking or stale dossiers → shock names. Equities only
(index/futures/FX tickers have no dossier value). Budget-driven cap keeps
evening cost predictable regardless of signal count.

CLI: python -m advisor.research.librarian_queue [--max 5]
Writes advisor/data/research/queue_<date>.json and prints it.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from advisor.journal import effective, read_all
from advisor.research.datastore import RESEARCH_DIR

ET = ZoneInfo("America/New_York")
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DOSSIERS = REPO_ROOT / "advisor" / "data" / "knowledge" / "dossiers"
STALE_DAYS = 7


def _is_equity(t: str | None) -> bool:
    return bool(t) and not any(x in t for x in ("^", "=F", "=X", "-USD", "/"))


def _dossier_age_days(ticker: str) -> float | None:
    meta = DOSSIERS / ticker / "meta.json"
    try:
        ts = json.loads(meta.read_text()).get("facts_refreshed")
        return (datetime.now(ET) - datetime.fromisoformat(ts)).days
    except Exception:
        return None            # no dossier


def build(max_n: int = 5) -> dict:
    queue: list[dict] = []
    seen: set[str] = set()

    def push(ticker: str, reason: str, mode: str) -> None:
        if len(queue) >= max_n or ticker in seen or not _is_equity(ticker):
            return
        seen.add(ticker)
        queue.append({"ticker": ticker, "reason": reason, "mode": mode})

    # 1. open advisor calls — always keep their dossiers warm
    raw = read_all()
    origin = {}
    for e in raw:
        origin.setdefault(e.get("id"), e.get("type"))
    for eid, e in effective(raw).items():
        if origin.get(eid) == "view" and e.get("status") == "open":
            push(e.get("yf_ticker"), f"open call {eid}", "refresh")

    # 2-5. factor sheet
    try:
        s = json.loads((RESEARCH_DIR / "signals_latest.json").read_text())
    except Exception:
        s = {}
    for side in ("longs", "shorts"):
        for x in s.get(side, []):
            if x.get("new_entrant"):
                push(x["ticker"], f"new entrant on {side} sheet", "new")
    for x in s.get("longs", [])[:6]:
        age = _dossier_age_days(x["ticker"])
        if age is None or age > STALE_DAYS:
            push(x["ticker"], f"top long (dossier {'missing' if age is None else f'{age:.0f}d stale'})",
                 "new" if age is None else "refresh")
    for x in s.get("shorts", [])[:3]:
        age = _dossier_age_days(x["ticker"])
        if age is None or age > STALE_DAYS:
            push(x["ticker"], f"top short (dossier {'missing' if age is None else f'{age:.0f}d stale'})",
                 "new" if age is None else "refresh")
    for x in s.get("shock_candidates", [])[:3]:
        push(x["ticker"], "shock/reversion candidate", "new")

    return {"date": datetime.now(ET).date().isoformat(),
            "as_of": datetime.now(ET).isoformat(),
            "candidates": queue}


def main() -> int:
    max_n = int(sys.argv[sys.argv.index("--max") + 1]) if "--max" in sys.argv else 5
    q = build(max_n)
    out = RESEARCH_DIR / f"queue_{q['date']}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(q, indent=2))
    print(json.dumps(q, indent=2))
    print(f"→ {out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
