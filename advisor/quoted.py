"""quoted — the advisor quote daemon (read-only IBKR + yfinance fallback).

One persistent connection, N consumers: subscribes market data for the tape
symbols + open-call tickers + watchlist names and writes a snapshot to
advisor/data/quotes/latest.json every ~2s (RTH) / 30s (off-hours). The
terminal tape and exit watcher read the FILE — zero network in their paths.

Honesty rules (memory: always show data source):
- marketDataType 3 = live where entitled (FX, SPX complex), delayed (~15min)
  elsewhere; every quote row carries src + live|delayed + its own timestamp.
- IBKR down → per-symbol yfinance fallback, labeled. The daemon degrades,
  never lies.
- READ-ONLY invariant: this process can never place an order; the Gateway
  API is additionally in read-only mode (keep it that way — TUNING_NOTES).

launchd: com.stockstest.advisor-quoted (KeepAlive)
CLI: python -m advisor.quoted [--once] [--interval 2]
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
REPO = Path(__file__).resolve().parent.parent
HOST = os.environ.get("ADVISOR_IB_HOST", "127.0.0.1")
PORT = int(os.environ.get("ADVISOR_IB_PORT", "4001"))
CLIENT_ID = int(os.environ.get("ADVISOR_IB_CLIENT_ID", "93"))
RTH_INTERVAL, OFF_INTERVAL = 2.0, 30.0
IB_UPDATE_FRESH_S = 120
YF_OPEN_MAX_AGE_S = 30 * 60
YF_CLOSED_MAX_AGE_S = 96 * 60 * 60
LOCKFILE = "/tmp/advisor-quoted.lock"

# always-on tape rows (terminal) — yf symbol keys
TAPE = ["^GSPC", "^NDX", "IWM", "^VIX", "^TNX", "CL=F", "GC=F", "SI=F",
        "EURUSD=X", "BTC-USD", "SMH", "TLT", "SPY", "QQQ"]
YF_ONLY = {"^TNX", "BTC-USD"}      # no clean read-only IBKR mapping/entitlement


def _data() -> Path:
    d = Path(os.environ.get("ADVISOR_DATA_DIR",
                            str(REPO / "advisor" / "data"))) / "quotes"
    d.mkdir(parents=True, exist_ok=True)
    return d


def market_open(now: datetime | None = None) -> bool:
    now = now or datetime.now(ET)
    return now.weekday() <= 4 and "09:25" <= now.strftime("%H:%M") <= "16:05"


def yf_quote_fresh(row: dict, now: datetime | None = None) -> bool:
    """Judge Yahoo data by its market-event time, never retrieval time."""
    now = now or datetime.now(ET)
    try:
        event = datetime.fromisoformat(row["market_ts"])
        if event.tzinfo is None:
            event = event.replace(tzinfo=ET)
        age = (now - event.astimezone(ET)).total_seconds()
        limit = YF_OPEN_MAX_AGE_S if market_open(now) else YF_CLOSED_MAX_AGE_S
        return 0 <= age <= limit
    except (KeyError, TypeError, ValueError):
        return False


def watch_symbols() -> list[str]:
    syms = list(TAPE)
    try:
        from advisor.research.outcomes import open_views
        syms += [e["yf_ticker"] for e in open_views() if e.get("yf_ticker")]
    except Exception:
        pass
    try:
        from advisor.watchlist import load as wl_load
        syms += [t for t, e in wl_load().items()
                 if e.get("state") in ("watchlist", "active_view")]
    except Exception:
        pass
    return list(dict.fromkeys(syms))


def to_contract(sym: str):
    """yf symbol → ib_insync contract (None → yfinance-only)."""
    from ib_insync import Forex, Index, Stock
    if sym in YF_ONLY:
        return None
    if sym == "^GSPC":
        return Index("SPX", "CBOE")
    if sym == "^NDX":
        return Index("NDX", "NASDAQ")
    if sym == "^VIX":
        return Index("VIX", "CBOE")
    if sym.endswith("=X"):
        return Forex(sym[:-2])
    if sym.endswith("=F"):
        return None                     # futures resolved dynamically below
    if any(x in sym for x in ("^", "/")):
        return None
    return Stock(sym, "SMART", "USD")


FUT_EXCH = {"CL": "NYMEX", "GC": "COMEX", "SI": "COMEX", "ES": "CME",
            "NQ": "CME"}


def resolve_future(ib, sym: str):
    from ib_insync import Future
    root = sym[:-2]
    exch = FUT_EXCH.get(root)
    if not exch:
        return None
    try:
        cds = ib.reqContractDetails(Future(root, exchange=exch))
        if not cds:
            return None
        front = sorted(cds, key=lambda cd: cd.contract.lastTradeDateOrContractMonth)
        return front[0].contract
    except Exception:
        return None


def _tick_px(t) -> tuple:
    """(px, kind) preferring last, then mid, then close."""
    import math
    def ok(v):
        return isinstance(v, (int, float)) and not math.isnan(v) and v > 0
    if ok(t.last):
        return t.last, "last"
    if ok(t.bid) and ok(t.ask):
        return (t.bid + t.ask) / 2, "mid"
    if ok(t.close):
        return t.close, "close"
    return None, None


class Daemon:
    def __init__(self) -> None:
        self.ib = None
        self.tickers: dict[str, object] = {}
        self.last_update_mono: dict[str, float] = {}
        self.last_update_ts: dict[str, str] = {}
        self.yf_cache: dict[str, dict] = {}
        self.yf_last = 0.0

    def connect(self) -> bool:
        try:
            from ib_insync import IB
            self.ib = IB()
            self.ib.connect(HOST, PORT, clientId=CLIENT_ID, timeout=12,
                            readonly=True)
            self.ib.reqMarketDataType(3)     # live where entitled, else delayed
            return True
        except Exception as exc:
            print(f"[quoted] IBKR connect failed: {exc}", flush=True)
            self.ib = None
            return False

    def subscribe(self, syms: list[str]) -> None:
        if not self.ib:
            return
        for s in syms:
            if s in self.tickers:
                continue
            c = to_contract(s)
            if c is None and s.endswith("=F"):
                c = resolve_future(self.ib, s)
            if c is None:
                continue
            try:
                self.ib.qualifyContracts(c)
                ticker = self.ib.reqMktData(c, "", False, False)
                self.tickers[s] = ticker
                def _mark_update(*_args, symbol=s):
                    self.last_update_mono[symbol] = time.monotonic()
                    self.last_update_ts[symbol] = datetime.now(ET).isoformat()
                ticker.updateEvent += _mark_update
            except Exception:
                continue

    def ib_quote_fresh(self, symbol: str) -> bool:
        last = self.last_update_mono.get(symbol)
        return last is not None and time.monotonic() - last <= IB_UPDATE_FRESH_S

    def yf_fill(self, syms: list[str]) -> None:
        """Fallback quotes for symbols IBKR can't serve (throttled to 60s)."""
        if time.time() - self.yf_last < 60:
            return
        self.yf_last = time.time()
        try:
            import yfinance as yf
            for s in syms:
                try:
                    # Intraday bars carry an exchange timestamp.  fast_info's
                    # last_price does not, so stamping it at retrieval time can
                    # make a stale quote look current.
                    hist = yf.Ticker(s).history(period="5d", interval="1m",
                                                prepost=True, auto_adjust=True)
                    closes = hist["Close"].dropna()
                    if closes.empty:
                        raise ValueError("no timestamped intraday bars")
                    event = closes.index[-1].to_pydatetime()
                    if event.tzinfo is None:
                        event = event.replace(tzinfo=ET)
                    event = event.astimezone(ET)
                    session = hist.loc[hist.index.date == event.date()]
                    prior = hist.loc[hist.index.date < event.date(), "Close"].dropna()
                    row = {
                        "px": round(float(closes.iloc[-1]), 4),
                        "prev_close": round(float(prior.iloc[-1]), 4) if len(prior) else None,
                        "day_low": round(float(session["Low"].min()), 4),
                        "day_high": round(float(session["High"].max()), 4),
                        "market_ts": event.isoformat(),
                        "ts": event.isoformat(),
                        "retrieved_at": datetime.now(ET).isoformat(),
                        "kind": "last", "type": "delayed",
                        "src": "yfinance timestamped intraday bar"}
                    if not yf_quote_fresh(row):
                        self.yf_cache.pop(s, None)
                        continue
                    self.yf_cache[s] = row
                except Exception:
                    self.yf_cache.pop(s, None)
                    continue
        except Exception as exc:
            print(f"[quoted] yf fill failed: {exc}", flush=True)

    def snapshot(self, syms: list[str]) -> dict:
        quotes: dict[str, dict] = {}
        ib_alive = bool(self.ib and self.ib.isConnected())
        for s in syms:
            t = self.tickers.get(s)
            if ib_alive and t is not None and self.ib_quote_fresh(s):
                px, kind = _tick_px(t)
                if px is not None:
                    mdt = getattr(t, "marketDataType", 3)
                    quotes[s] = {
                        "px": round(float(px), 4),
                        "bid": round(float(t.bid), 4) if t.bid and t.bid > 0 else None,
                        "ask": round(float(t.ask), 4) if t.ask and t.ask > 0 else None,
                        "prev_close": round(float(t.close), 4)
                        if t.close and t.close > 0 else None,
                        "ts": self.last_update_ts[s],
                        "received_age_s": round(
                            time.monotonic() - self.last_update_mono[s], 1),
                        "kind": kind,
                        "type": "live" if mdt == 1 else "delayed",
                        "src": f"IBKR {'live' if mdt == 1 else 'delayed ~15min'}"}
                    continue
            if s in self.yf_cache and yf_quote_fresh(self.yf_cache[s]):
                quotes[s] = self.yf_cache[s]
        return {"as_of": datetime.now(ET).isoformat(),
                "ib_connected": ib_alive,
                "market_open": market_open(),
                "quotes": quotes}

    def write(self, snap: dict) -> None:
        p = _data() / "latest.json"
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(snap))
        os.replace(tmp, p)

    def run(self, once: bool = False, interval: float | None = None) -> None:
        import fcntl
        lock = open(LOCKFILE, "w")
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("[quoted] another instance running — exiting")
            return
        print(f"[quoted] up — IBKR {HOST}:{PORT} clientId {CLIENT_ID} "
              f"(read-only), tape+calls+watchlist", flush=True)
        self.connect()
        last_syms: list[str] = []
        last_resub = 0.0
        while True:
            try:
                if self.ib and not self.ib.isConnected():
                    print("[quoted] IBKR dropped — reconnecting", flush=True)
                    self.tickers.clear()
                    self.last_update_mono.clear()
                    self.last_update_ts.clear()
                    self.connect()
                if not self.ib:
                    time.sleep(15)
                    self.connect()
                # refresh symbol set every 5 min (open calls change)
                if time.time() - last_resub > 300:
                    last_resub = time.time()
                    syms = watch_symbols()
                    if syms != last_syms:
                        last_syms = syms
                        self.subscribe(syms)
                if self.ib and self.ib.isConnected():
                    self.ib.sleep(0.5)       # let ticks flow
                ib_served = {s for s in last_syms if s in self.tickers
                             and self.ib_quote_fresh(s)} \
                    if (self.ib and self.ib.isConnected()) else set()
                self.yf_fill([s for s in last_syms if s not in ib_served])
                snap = self.snapshot(last_syms)
                self.write(snap)
                if once:
                    print(json.dumps({k: v for k, v in snap.items()
                                      if k != "quotes"}, indent=2))
                    print(f"quotes: {len(snap['quotes'])}/{len(last_syms)}")
                    return
                time.sleep(interval or (RTH_INTERVAL if market_open()
                                        else OFF_INTERVAL))
            except KeyboardInterrupt:
                raise
            except Exception as exc:
                print(f"[quoted] loop error ({type(exc).__name__}: {exc}) — "
                      f"retry in 30s", flush=True)
                time.sleep(30)


def main() -> int:
    once = "--once" in sys.argv
    interval = None
    if "--interval" in sys.argv:
        interval = float(sys.argv[sys.argv.index("--interval") + 1])
    Daemon().run(once=once, interval=interval)
    return 0


if __name__ == "__main__":
    sys.exit(main())
