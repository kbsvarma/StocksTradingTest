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
from advisor.journal import effective

ET = ZoneInfo("America/New_York")
REPO_ROOT = Path(__file__).resolve().parent.parent
POLL_SECONDS = 300          # 5-min cadence — these are swing levels, not 0DTE SL
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


def batch_prices(tickers: list[str]) -> dict[str, tuple]:
    """One batched 1m-bar request for all watched tickers.

    Returns {ticker: (price, source_label)} — only FRESH quotes (last bar
    within STALE_MINUTES). A stale/no-data ticker is simply absent: the
    watcher must never fire an exit signal off a dead feed (2026-06-12
    loop-pass-3; same class of failure as the bot's stale-yfinance-LIMIT
    incident of 2026-05-20).
    """
    out: dict[str, tuple] = {}
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
        msgs.append(
            f"🛑 EXIT SIGNAL — STOP HIT  [{eid}]\n"
            f"{e.get('instrument', tick)} {'long' if long_ else 'short'} — "
            f"price {px} crossed stop {stop}\n"
            f"Recommendation: EXIT NOW. Thesis invalidated per the original call.\n"
            f"({src})"
        )
    if tgt_hit and not st.get("target"):
        st["target"] = datetime.now(ET).isoformat()
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
    n = 0
    prices = batch_prices(sorted({e["yf_ticker"] for e in calls}))
    for e in calls:
        got = prices.get(e["yf_ticker"])
        if not got:
            if verbose:
                print(f"[watcher] {e['yf_ticker']}: no fresh quote — skipped")
            continue
        px, src = got
        for msg in check_call(e, px, src, alerted):
            telegram_io.send(msg)
            n += 1
        if verbose:
            print(f"[watcher] {e['yf_ticker']} px={px} stop={e['stop_px']} "
                  f"target={e['target_px']}")
    _save_alerted(alerted)
    return n


def run() -> None:
    lock = open(LOCKFILE, "w")
    try:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print("[watcher] another instance running — exiting")
        return
    print(f"[watcher] up — polling every {POLL_SECONDS}s during RTH", flush=True)
    while True:
        try:
            if market_open():
                sent = scan_once()
                if sent:
                    print(f"[watcher] sent {sent} alert(s)", flush=True)
                time.sleep(POLL_SECONDS)
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
