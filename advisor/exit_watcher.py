"""Live entry/exit-level watcher — the deterministic fast loop for advisor calls.

Polls every open decision-journal view during market hours and sends a
Telegram alert the moment price touches its entry zone ("when to buy"),
target, or stop ("when to exit"). This is code, not a model: levels written
by the advisor are watched mechanically, the same architecture rule as the
bot's SL monitor.

Scope: journal entries with type=view, status=open, and numeric fields
(yf_ticker, target_px, stop_px, optional entry_px_low/high). Executed Tier 1
positions are NOT watched here — they have the bot's own SL monitor.

Alert-only: this process can never place an order.

State: advisor/data/watcher_alerts.json — one alert per (call, level), no spam.
       advisor/data/excursions.json — running max/min price per open call
       (gives MAE/MFE for the learning loop for free, 2026-07-01).
       On a level hit it also appends a machine `resolve_pending` row to the
       journal (facts only — the final resolve with outcome_tag is written by
       the post-mortem/weekly session, never by the watcher).

Run:  python -m advisor.exit_watcher [--once]   (launchd: KeepAlive)
"""
from __future__ import annotations

import fcntl
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from advisor import telegram_io
from advisor.journal import append_raw, effective

ET = ZoneInfo("America/New_York")
REPO_ROOT = Path(__file__).resolve().parent.parent
POLL_SECONDS = 300          # 5-min cadence — these are swing levels, not 0DTE SL
STORE_POLL_SECONDS = 30     # cadence when the quote daemon's store is fresh
OFF_HOURS_SECONDS = 600
LOCKFILE = "/tmp/advisor-exit-watcher.lock"


def _data_dir() -> Path:
    d = Path(os.environ.get("ADVISOR_DATA_DIR", str(REPO_ROOT / "advisor" / "data")))
    d.mkdir(parents=True, exist_ok=True)
    return d


def _alerts_path() -> Path:
    return _data_dir() / "watcher_alerts.json"


def _load_alerted() -> dict:
    try:
        return json.loads(_alerts_path().read_text())
    except Exception:
        return {}


def _save_alerted(d: dict) -> None:
    tmp = _alerts_path().with_suffix(".json.tmp")
    tmp.write_text(json.dumps(d, indent=2))
    os.replace(tmp, _alerts_path())


def _excursions_path() -> Path:
    return _data_dir() / "excursions.json"


def _load_excursions() -> dict:
    try:
        return json.loads(_excursions_path().read_text())
    except Exception:
        return {}


def _save_excursions(d: dict) -> None:
    tmp = _excursions_path().with_suffix(".json.tmp")
    tmp.write_text(json.dumps(d, indent=2))
    os.replace(tmp, _excursions_path())


def _track_excursion(exc: dict, eid: str, px: float) -> None:
    now = datetime.now(ET).isoformat()
    e = exc.setdefault(eid, {"max_px": px, "min_px": px, "first_ts": now, "n_obs": 0})
    e["max_px"] = max(e["max_px"], px)
    e["min_px"] = min(e["min_px"], px)
    e["last_ts"] = now
    e["n_obs"] = e.get("n_obs", 0) + 1


def market_open(now: datetime | None = None) -> bool:
    now = now or datetime.now(ET)
    if now.weekday() > 4:
        return False
    hm = now.strftime("%H:%M")
    return "09:30" <= hm <= "16:00"


def watchable_calls() -> list[dict]:
    out = []
    for eid, e in effective().items():
        if e.get("type") != "view" or e.get("status") != "open":
            continue
        if not e.get("yf_ticker"):
            continue
        if not isinstance(e.get("stop_px"), (int, float)) \
                or not isinstance(e.get("target_px"), (int, float)):
            continue
        out.append(e)
    return out


STALE_MINUTES = 30   # never alert on a quote older than this during RTH
STORE_FRESH_S = 90   # quote-daemon snapshot age we trust


def store_prices(tickers: list[str]) -> dict[str, tuple]:
    """Quote-daemon path (2026-07-02): read advisor/data/quotes/latest.json —
    zero network, seconds-fresh where IBKR serves it. Only rows whose OWN
    timestamp is fresh are used; anything else falls through to yfinance."""
    out: dict[str, tuple] = {}
    try:
        snap = json.loads((_data_dir() / "quotes" / "latest.json").read_text())
        now = datetime.now(ET)
        snap_age = (now - datetime.fromisoformat(snap["as_of"])).total_seconds()
        if snap_age > STORE_FRESH_S:
            return out
        for t in tickers:
            q = snap.get("quotes", {}).get(t)
            if not q:
                continue
            q_age = (now - datetime.fromisoformat(q["ts"])).total_seconds()
            # delayed feeds carry ~15min embedded lag; the row ts is write
            # time — accept writes ≤STORE_FRESH_S and label the lag honestly
            if q_age > STORE_FRESH_S:
                continue
            out[t] = (round(float(q["px"]), 4),
                      f"quoted:{q['src']} ({q['kind']})")
    except Exception:
        return {}
    return out


def batch_prices(tickers: list[str]) -> dict[str, tuple]:
    """Quote store first (fast path), yfinance 1m bars for the remainder.

    Returns {ticker: (price, source_label)} — only FRESH quotes (last bar
    within STALE_MINUTES). A stale/no-data ticker is simply absent: the
    watcher must never fire an exit signal off a dead feed (2026-06-12
    loop-pass-3; same class of failure as the bot's stale-yfinance-LIMIT
    incident of 2026-05-20).
    """
    out: dict[str, tuple] = store_prices(tickers)
    tickers = [t for t in tickers if t not in out]
    if not tickers:
        return out
    try:
        import pandas as pd
        import yfinance as yf
        data = yf.download(tickers, period="1d", interval="1m", progress=False,
                           group_by="ticker", threads=True)
        now = datetime.now(ET)
        for t in tickers:
            try:
                closes = (data[t]["Close"] if len(tickers) > 1 else data["Close"]).dropna()
                if not len(closes):
                    continue
                ts = closes.index[-1]
                ts = ts.tz_convert(ET) if ts.tzinfo else ts.tz_localize("UTC").tz_convert(ET)
                age_min = (now - ts).total_seconds() / 60
                if age_min > STALE_MINUTES:
                    print(f"[watcher] {t}: quote stale ({age_min:.0f}min) — skipping",
                          flush=True)
                    continue
                out[t] = (round(float(closes.iloc[-1]), 4),
                          f"yfinance 1m (bar {ts.strftime('%H:%M')} ET, ~15min delay)")
            except Exception:
                continue
    except Exception as exc:
        print(f"[watcher] batch fetch failed: {exc}", flush=True)
    return out


def fetch_price(ticker: str):
    """Single-ticker fallback (kept for --once debugging)."""
    px = batch_prices([ticker]).get(ticker)
    return px if px else (None, "no fresh data")


def _journal_hit(eid: str, level: str, px: float, src: str) -> None:
    """Record the fact of a level hit (facts only — resolution stays human)."""
    try:
        append_raw({"id": eid, "ts": datetime.now(ET).isoformat(),
                    "type": "resolve_pending", "hit_level": level,
                    "hit_px": px, "hit_ts": datetime.now(ET).isoformat(),
                    "hit_src": src})
    except Exception as exc:   # journaling must never block the alert path
        print(f"[watcher] resolve_pending append failed for {eid}: {exc}",
              flush=True)


def check_call(e: dict, px: float, src: str, alerted: dict) -> list[str]:
    """Return alert messages for newly-crossed levels of one call."""
    eid = e["id"]
    long_ = (e.get("direction", "long") or "long").lower() != "short"
    st = alerted.setdefault(eid, {})
    msgs = []
    tick = e["yf_ticker"]
    stop, target = float(e["stop_px"]), float(e["target_px"])

    stop_hit = px <= stop if long_ else px >= stop
    tgt_hit = px >= target if long_ else px <= target

    if stop_hit and not st.get("stop"):
        st["stop"] = datetime.now(ET).isoformat()
        _journal_hit(eid, "stop", px, src)
        msgs.append(
            f"🛑 EXIT SIGNAL — STOP HIT  [{eid}]\n"
            f"{e.get('instrument', tick)} {'long' if long_ else 'short'} — "
            f"price {px} crossed stop {stop}\n"
            f"Recommendation: EXIT NOW. Thesis invalidated per the original call.\n"
            f"({src})"
        )
    if tgt_hit and not st.get("target"):
        st["target"] = datetime.now(ET).isoformat()
        _journal_hit(eid, "target", px, src)
        msgs.append(
            f"🎯 EXIT SIGNAL — TARGET HIT  [{eid}]\n"
            f"{e.get('instrument', tick)} {'long' if long_ else 'short'} — "
            f"price {px} reached target {target}\n"
            f"Recommendation: take profit (or trail stop if momentum strong).\n"
            f"({src})"
        )
    lo, hi = e.get("entry_px_low"), e.get("entry_px_high")
    if isinstance(lo, (int, float)) and isinstance(hi, (int, float)) \
            and lo <= px <= hi and not st.get("entry") \
            and not st.get("stop") and not st.get("target"):
        st["entry"] = datetime.now(ET).isoformat()
        msgs.append(
            f"🟢 BUY ZONE TOUCHED  [{eid}]\n"
            f"{e.get('instrument', tick)} — price {px} inside entry zone {lo}-{hi}\n"
            f"Per the call: {'enter long' if long_ else 'enter short'}, "
            f"stop {stop}, target {target}\n"
            f"({src})"
        )
    return msgs


def scan_once(verbose: bool = False) -> int:
    calls = watchable_calls()
    if verbose:
        print(f"[watcher] {len(calls)} open watchable call(s)")
    if not calls:
        return 0
    alerted = _load_alerted()
    excursions = _load_excursions()
    n = 0
    prices = batch_prices(sorted({e["yf_ticker"] for e in calls}))
    for e in calls:
        got = prices.get(e["yf_ticker"])
        if not got:
            if verbose:
                print(f"[watcher] {e['yf_ticker']}: no fresh quote — skipped")
            continue
        px, src = got
        _track_excursion(excursions, e["id"], px)
        for msg in check_call(e, px, src, alerted):
            telegram_io.send(msg)
            n += 1
        if verbose:
            print(f"[watcher] {e['yf_ticker']} px={px} stop={e['stop_px']} "
                  f"target={e['target_px']}")
    _save_alerted(alerted)
    _save_excursions(excursions)
    return n


def run() -> None:
    lock = open(LOCKFILE, "w")
    try:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print("[watcher] another instance running — exiting")
        return
    print(f"[watcher] up — {STORE_POLL_SECONDS}s cadence on the quote store, "
          f"{POLL_SECONDS}s on yfinance fallback", flush=True)
    while True:
        try:
            if market_open():
                sent = scan_once()
                if sent:
                    print(f"[watcher] sent {sent} alert(s)", flush=True)
                # quote daemon fresh → tight loop (reads are local file I/O);
                # daemon down → old 5-min yfinance cadence
                fast = bool(store_prices(
                    [e["yf_ticker"] for e in watchable_calls()][:1]))
                time.sleep(STORE_POLL_SECONDS if fast else POLL_SECONDS)
            else:
                time.sleep(OFF_HOURS_SECONDS)
        except KeyboardInterrupt:
            raise
        except Exception as exc:
            print(f"[watcher] error ({type(exc).__name__}: {exc}) — retry in 60s",
                  flush=True)
            time.sleep(60)


if __name__ == "__main__":
    if "--once" in sys.argv:
        scan_once(verbose=True)
    else:
        run()
