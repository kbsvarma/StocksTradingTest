"""Watchlist state machine — "right idea, wrong price" never evaporates.

States: candidate → researched → watchlist → active_view → resolved → dormant.
A `watchlist` entry MUST carry a numeric trigger (px_below/px_above) or an
event note, and an expiry (default 20 trading days → dormant). --check
evaluates triggers against delayed quotes and marks `triggered` — the
morning synthesis treats triggered entries as its warmest leads.

State: advisor/data/knowledge/watchlist.json (atomic rewrite)
Log:   advisor/data/knowledge/watchlist_log.jsonl (append-only transitions)

CLI:
  python -m advisor.watchlist --set DECK --state watchlist \
      --trigger-px 92 --trigger-dir below --expires 2026-07-30 \
      --note "PEAD long, wait for gap-fill" --source pead_fresh
  python -m advisor.watchlist --set DECK --state active_view --journal-id V-...
  python -m advisor.watchlist --list [--state watchlist]
  python -m advisor.watchlist --check          # evaluate triggers (delayed quotes)
  python -m advisor.watchlist --sweep          # expire past-expiry entries
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
REPO_ROOT = Path(__file__).resolve().parent.parent

STATES = ("candidate", "researched", "watchlist", "active_view",
          "resolved", "dormant")


def _dir() -> Path:
    d = Path(os.environ.get("ADVISOR_DATA_DIR",
                            str(REPO_ROOT / "advisor" / "data"))) / "knowledge"
    d.mkdir(parents=True, exist_ok=True)
    return d


def load() -> dict:
    try:
        return json.loads((_dir() / "watchlist.json").read_text())
    except Exception:
        return {}


def _save(d: dict) -> None:
    p = _dir() / "watchlist.json"
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(d, indent=2))
    os.replace(tmp, p)


def _log(ticker: str, frm: str | None, to: str, by: str, reason: str) -> None:
    with (_dir() / "watchlist_log.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": datetime.now(ET).isoformat(), "ticker": ticker,
                            "from": frm, "to": to, "by": by,
                            "reason": reason}) + "\n")


def set_state(ticker: str, state: str, *, by: str = "cli", reason: str = "",
              trigger_px: float | None = None, trigger_dir: str | None = None,
              expires: str | None = None, note: str = "",
              source: str = "", journal_id: str = "") -> dict:
    if state not in STATES:
        raise ValueError(f"state must be one of {STATES}")
    wl = load()
    prev = wl.get(ticker, {})
    if state == "watchlist":
        if trigger_px is None and not note:
            raise ValueError("watchlist entries need --trigger-px or an event --note")
        if trigger_px is not None and trigger_dir not in ("below", "above"):
            raise ValueError("--trigger-px needs --trigger-dir below|above")
        if not expires:
            expires = (datetime.now(ET) + timedelta(days=28)).date().isoformat()
    entry = {
        "ticker": ticker, "state": state,
        "since": datetime.now(ET).isoformat(),
        "note": note or prev.get("note", ""),
        "source": source or prev.get("source", ""),
        "journal_id": journal_id or prev.get("journal_id", ""),
    }
    if trigger_px is not None:
        entry["trigger"] = {"px": trigger_px, "dir": trigger_dir}
    elif prev.get("trigger") and state == "watchlist":
        entry["trigger"] = prev["trigger"]
    if expires:
        entry["expires"] = expires
    elif prev.get("expires") and state == "watchlist":
        entry["expires"] = prev["expires"]
    if prev.get("triggered"):
        entry["triggered"] = prev["triggered"]
    wl[ticker] = entry
    _save(wl)
    _log(ticker, prev.get("state"), state, by, reason or note)
    return entry


def sweep() -> list[str]:
    """Expire watchlist entries past their expiry → dormant."""
    wl = load()
    today = datetime.now(ET).date().isoformat()
    expired = []
    for t, e in wl.items():
        if e.get("state") == "watchlist" and e.get("expires", "9999") < today:
            e["state"] = "dormant"
            e["since"] = datetime.now(ET).isoformat()
            _log(t, "watchlist", "dormant", "sweep", "expired")
            expired.append(t)
    if expired:
        _save(wl)
    return expired


def check() -> list[dict]:
    """Evaluate price triggers on delayed quotes; mark newly-triggered."""
    wl = load()
    watch = {t: e for t, e in wl.items()
             if e.get("state") == "watchlist" and e.get("trigger")
             and not e.get("triggered")}
    if not watch:
        return []
    hits = []
    try:
        import yfinance as yf
        for t, e in watch.items():
            try:
                px = float(yf.Ticker(t).fast_info.last_price)
            except Exception:
                continue
            trg = e["trigger"]
            hit = px <= trg["px"] if trg["dir"] == "below" else px >= trg["px"]
            if hit:
                e["triggered"] = {"ts": datetime.now(ET).isoformat(),
                                  "px": round(px, 4),
                                  "src": "yfinance delayed ~15min"}
                _log(t, "watchlist", "watchlist", "check",
                     f"TRIGGERED @ {px:.2f} ({trg['dir']} {trg['px']})")
                hits.append({**e})
    except Exception as exc:
        print(f"[watchlist] check failed: {exc}", file=sys.stderr)
    if hits:
        _save(wl)
    return hits


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--set")
    ap.add_argument("--state")
    ap.add_argument("--trigger-px", type=float)
    ap.add_argument("--trigger-dir", choices=("below", "above"))
    ap.add_argument("--expires")
    ap.add_argument("--note", default="")
    ap.add_argument("--source", default="")
    ap.add_argument("--journal-id", default="")
    ap.add_argument("--by", default="cli")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--sweep", action="store_true")
    a = ap.parse_args()

    if a.set:
        if not a.state:
            ap.error("--set requires --state")
        e = set_state(a.set.upper(), a.state, by=a.by, note=a.note,
                      trigger_px=a.trigger_px, trigger_dir=a.trigger_dir,
                      expires=a.expires, source=a.source,
                      journal_id=a.journal_id)
        print(json.dumps(e, indent=2))
        return 0
    if a.sweep:
        ex = sweep()
        print(f"expired → dormant: {ex or 'none'}")
        return 0
    if a.check:
        hits = check()
        for h in hits:
            print(f"⚡ TRIGGERED {h['ticker']} @ {h['triggered']['px']} "
                  f"({h['trigger']['dir']} {h['trigger']['px']}) — {h['note']}")
        if not hits:
            print("no new triggers")
        return 0
    if a.list:
        wl = load()
        for t, e in sorted(wl.items()):
            if a.state and e.get("state") != a.state:
                continue
            trg = e.get("trigger")
            print(f"{t:<6} {e['state']:<12} "
                  f"{'trg ' + e['trigger']['dir'] + ' ' + str(trg['px']) if trg else '':<18}"
                  f"{'⚡' + e['triggered']['ts'][:10] if e.get('triggered') else '':<13}"
                  f"exp {e.get('expires', '—'):<12} {e.get('note', '')[:50]}")
        return 0
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
