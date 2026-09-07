"""Intraday filing poller — 8-K / Form 4 on held + watchlist names.

Deterministic source intake with downstream thesis reassessment. Every 20 min
between 06:00 and 22:00 ET on weekdays (including after-hours earnings):
poll EDGAR submissions for names we hold or watch; NEW filings since last
seen → one Telegram ping + a row in advisor/data/alerts/intraday_alerts.jsonl
(the terminal's alert center reads it). Dedup: one ping per (ticker, form,
accession). launchd: com.stockstest.advisor-events (StartInterval 1200).

CLI: python -m advisor.events_poller [--once] [--force-open]
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
REPO = Path(__file__).resolve().parent.parent
FORMS = ("8-K", "4", "10-Q", "10-K", "SC 13D", "SC 13G")


def _data() -> Path:
    return Path(os.environ.get("ADVISOR_DATA_DIR",
                               str(REPO / "advisor" / "data")))


def _seen_path() -> Path:
    return _data() / "events_seen.json"


def filing_poll_open(now: datetime | None = None) -> bool:
    now = now or datetime.now(ET)
    # This is a filing-intake window, not a claim that the exchange is open.
    return now.weekday() <= 4 and "06:00" <= now.strftime("%H:%M") <= "22:00"


def targets() -> list[str]:
    """Held (open journal views) + watchlist-state names, equities only."""
    out: set[str] = set()
    try:
        from advisor.research.outcomes import open_views
        for e in open_views():
            t = e.get("yf_ticker") or ""
            if t and not any(x in t for x in ("^", "=F", "=X", "-USD")):
                out.add(t)
    except Exception:
        pass
    try:
        from advisor.watchlist import load as wl_load
        for t, e in wl_load().items():
            if e.get("state") in ("watchlist", "active_view"):
                out.add(t)
    except Exception:
        pass
    try:
        from advisor.intelligence.adapters import tracked_symbols
        out.update(tracked_symbols(_data()))
    except (OSError, ValueError):
        pass
    return sorted(out)


def poll_once(force: bool = False) -> int:
    if not force and not filing_poll_open():
        return 0
    names = targets()
    if not names:
        return 0
    try:
        seen = json.loads(_seen_path().read_text())
    except Exception:
        seen = {}
    from advisor import telegram_io
    from advisor.research.edgar import recent_filings
    alerts_dir = _data() / "alerts"
    alerts_dir.mkdir(parents=True, exist_ok=True)
    n_new = 0
    for t in names:
        try:
            filings = recent_filings(t, forms=FORMS, limit=6)
        except Exception:
            continue
        known = set(seen.get(t, []))
        for f in filings:
            key = f["url"]
            if key in known:
                continue
            known.add(key)
            # only ping on filings from the last 3 days (first run seeds
            # history silently instead of spamming six old filings per name)
            fresh = f.get("filed", "") >= (datetime.now(ET).date()
                                           .fromordinal(datetime.now(ET).date()
                                                        .toordinal() - 3)
                                           .isoformat())
            if not fresh:
                continue
            n_new += 1
            row = {"ts": datetime.now(ET).isoformat(), "kind": "filing",
                   "ticker": t, "form": f["form"], "filed": f["filed"],
                   "url": f["url"], "src": "EDGAR submissions"}
            with (alerts_dir / "intraday_alerts.jsonl").open("a") as fh:
                fh.write(json.dumps(row) + "\n")
            telegram_io.send(
                f"📄 NEW FILING — {t} {f['form']} (filed {f['filed']})\n"
                f"{f['url']}\n(held/watched name — review if material)")
        seen[t] = sorted(known)[-40:]
    tmp = _seen_path().with_suffix(".json.tmp")
    tmp.write_text(json.dumps(seen, indent=1))
    os.replace(tmp, _seen_path())
    if n_new:
        try:
            from advisor.intelligence.worker import run as reassess
            reassess(_data())
        except Exception as exc:
            print(f"[events] intelligence reassessment failed: {type(exc).__name__}", flush=True)
    return n_new


def main() -> int:
    force = "--force-open" in sys.argv
    n = poll_once(force=force)
    print(f"[events] {datetime.now(ET).strftime('%H:%M')} — "
          f"{n} new filing alert(s)"
          + ("" if filing_poll_open() or force else " (outside filing intake window — skipped)"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
