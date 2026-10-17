"""Bar store + IBKR data client.

Per instrument we keep state/bars_{SYM}.parquet: 60m bars, UTC index (bar
START), columns open/high/low/close/volume/source. Seeded once from the local
Databento caches (seed_history.py), then extended every wake from the LIVE
gateway's historical API (micro contract prices — same index points as the
full-size seed contracts; documented seam).
"""
from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd
from ib_insync import IB, ContFuture

log = logging.getLogger("trend_bot.data")

BASE = Path(__file__).resolve().parent


class BarStore:
    def __init__(self, state_dir: Path):
        self.dir = Path(state_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self._cache: dict[str, pd.DataFrame] = {}

    def path(self, sym: str) -> Path:
        return self.dir / f"bars_{sym}.parquet"

    def load(self, sym: str) -> pd.DataFrame:
        if sym not in self._cache:
            p = self.path(sym)
            if p.exists():
                self._cache[sym] = pd.read_parquet(p)
            else:
                self._cache[sym] = pd.DataFrame(
                    columns=["open", "high", "low", "close", "volume", "source"])
        return self._cache[sym]

    def append(self, sym: str, new: pd.DataFrame, source: str) -> int:
        """Add bars strictly after the stored end; never rewrite history.
        The last stored live bar may still have been forming when written, so
        one overlapping bar from the same source is allowed to refresh it.
        Missing bars inside the current contract segment may also be repaired
        from a later broker response; bars from another contract are never
        allowed to backfill across a roll seam."""
        cur = self.load(sym)
        new = new.copy()
        new["source"] = source
        if len(cur):
            seed_end = cur.index.max()
            repair = new.iloc[0:0]
            # Allow refresh of the final live bar. Once contract identity is
            # available, also admit previously absent timestamps from the
            # same stored contract segment so a transient IB history gap can
            # heal on the next fetch without rewriting completed bars.
            if source == "ibkr" and len(cur[cur["source"] == "ibkr"]):
                live = cur[cur["source"] == "ibkr"]
                last_live = live.index.max()
                if "contract" in new.columns and "contract" in live.columns:
                    pieces = []
                    for contract, group in new.groupby("contract",
                                                       dropna=False):
                        same = live[live["contract"] == contract]
                        if len(same):
                            candidate = group[group.index >= same.index.min()]
                            pieces.append(candidate[
                                ~candidate.index.isin(cur.index)])
                    if pieces:
                        repair = pd.concat(pieces)
                cur = cur[~((cur.index == last_live) & (cur["source"] == "ibkr")
                            & cur.index.isin(new.index))]
                forward = new[new.index >= last_live]
                new = pd.concat([repair, forward]).sort_index()
                new = new[~new.index.duplicated(keep="last")]
            else:
                new = new[new.index > seed_end]
        if not len(new):
            return 0
        out = pd.concat([cur, new]).sort_index() if len(cur) else new.sort_index()
        out = out[~out.index.duplicated(keep="last")]
        self._cache[sym] = out
        tmp = self.path(sym).with_suffix(".tmp.parquet")
        out.to_parquet(tmp)
        tmp.replace(self.path(sym))
        return len(new)

    def bars(self, sym: str, tail: int | None = None) -> pd.DataFrame:
        """Complete bars only (drops the still-forming current hour)."""
        raw = self.load(sym)
        cols = [c for c in ("open", "high", "low", "close", "volume")
                if c in raw.columns]
        B = raw[cols]
        if not len(B):
            # a brand-new store has a RangeIndex; comparing that against a
            # Timestamp raises (first live wake of the satellite _S symbols)
            return B
        now = pd.Timestamp.now(tz="UTC")
        B = B[B.index < now.floor("h")]
        return B.tail(tail) if tail else B

    def daily_closes(self, sym: str) -> pd.Series:
        """Last 60m close per UTC calendar day (rotation input — matches the
        reference engine's daily-series construction)."""
        c = self.load(sym)["close"]
        return c.resample("1D").last().dropna()


class IBKRData:
    """Read-only client on the LIVE gateway (it owns the data subscriptions)."""

    # IB system-message codes that mark the gateway going cold / coming back
    COLD_CODES = {1100: "connectivity_lost", 2103: "farm_broken",
                  2105: "hmds_farm_broken", 2110: "connectivity_broken"}
    ALIVE_CODES = {1101: "connectivity_restored_lost_state",
                   1102: "connectivity_restored",
                   2104: "farm_ok", 2106: "hmds_farm_ok", 2158: "sec_farm_ok"}

    def __init__(self, host: str, port: int, client_id: int, dlog=None):
        self.host, self.port, self.client_id = host, port, client_id
        self.ib = IB()
        self.dlog = dlog
        self._was_connected = False
        self._down_since = None
        self._events_wired = False

    def _mono(self):
        import time as _t
        return _t.monotonic()

    def _wire_events(self):
        if self._events_wired or not self.dlog:
            return
        self._events_wired = True

        def on_disconnect():
            self._down_since = self._mono()
            self.dlog.log("connections", client="data", port=self.port,
                          event="socket_disconnected",
                          note="GATEWAY COLD — socket dropped")

        def on_err(reqId, code, msg, *a):
            if code in self.COLD_CODES:
                self._down_since = self._down_since or self._mono()
                self.dlog.log("connections", client="data", event="gateway_cold",
                              code=code, kind=self.COLD_CODES[code],
                              msg=str(msg)[:160])
            elif code in self.ALIVE_CODES:
                out = (round(self._mono() - self._down_since, 1)
                       if self._down_since else None)
                self._down_since = None
                self.dlog.log("connections", client="data", event="gateway_alive",
                              code=code, kind=self.ALIVE_CODES[code],
                              outage_s=out, msg=str(msg)[:160])

        self.ib.disconnectedEvent += on_disconnect
        self.ib.errorEvent += on_err

    def connect(self):
        if not self.ib.isConnected():
            self.ib.connect(self.host, self.port, clientId=self.client_id,
                            readonly=True, timeout=20)
            # bound every API request: a flapping data farm makes untimed
            # reqHistoricalData hang FOREVER (wedged the bot 3x on
            # 2026-07-16; watchdog kept killing, wake kept re-wedging).
            # On timeout the call raises -> wake error path -> 5-min retry.
            self.ib.RequestTimeout = 90
            self._wire_events()
            if self.dlog:
                out = (round(self._mono() - self._down_since, 1)
                       if self._down_since else None)
                self.dlog.log("connections", client="data", port=self.port,
                              event="reconnect" if self._was_connected
                              else "connect", outage_s=out,
                              note="GATEWAY BACK ALIVE" if out else None)
            self._was_connected = True
            self._down_since = None
        return self.ib

    def disconnect(self):
        if self.ib.isConnected():
            self.ib.disconnect()

    def vix_snapshot(self) -> float | None:
        """Prior/current VIX level via CBOE index feed (observation-only,
        feeds the pre-registered regime forward trial — PREREG_REGIME_FORWARD.md).
        Never raises; None on any failure."""
        try:
            from ib_insync import Index
            c = Index("VIX", "CBOE")
            self.ib.qualifyContracts(c)
            # 2026-07-10 audit: returned None on EVERY wake since launch
            # (0/392 decision rows stamped) and the old code swallowed the
            # reason. Now: streaming (not snapshot) request, live then
            # delayed-frozen fallback, failure mode dlogged.
            t = None
            for mdt in (1, 4):                # live, then delayed-frozen
                try:
                    self.ib.reqMarketDataType(mdt)
                    t = self.ib.reqMktData(c, "", False, False)
                    self.ib.sleep(3)
                    self.ib.cancelMktData(c)
                    for v in (t.last, t.close, t.markPrice):
                        if v is not None and v == v and v > 0:
                            if self.dlog:
                                self.dlog.log("connections", event="vix_ok",
                                              mdt=mdt, vix=float(v))
                            return float(v)
                finally:
                    self.ib.reqMarketDataType(1)
            if self.dlog and t is not None:
                self.dlog.log("connections", event="vix_empty",
                              last=str(t.last), close=str(t.close),
                              mark=str(t.markPrice))
        except Exception as e:
            if self.dlog:
                self.dlog.log("connections", event="vix_error",
                              error=str(e)[:200])
        return None

    def front_contract(self, trade_symbol: str, exchange: str,
                       min_days_to_expiry: int = 0):
        """The contract new orders should use.

        Default (min_days_to_expiry=0) is pure ContFuture — IBKR's own
        liquidity-based front month.  ROLL_SPLICE_SPEC.md pins live roll
        timing to that switch (±0-1 days vs the research volume-roll), so
        cash-settled index futures MUST keep this path untouched.

        Physically-deliverable products (COMEX metals) need more: IBKR
        refuses to OPEN them ahead of first notice day, well before expiry.
        On 2026-07-29 an MGCQ6 buy was rejected with error 201 ("does not
        comply with our order handling rules for derivatives that result in
        physical delivery") at 29 days to expiry, which latched a global
        entry halt.  For those instruments config sets a positive
        min_days_to_expiry and we step forward to the nearest month that
        clears it.  Selecting a different conId is all that is needed — the
        existing roll path force-closes a held position when the front
        conId changes, and BarStore.append keeps the contract seam intact.
        """
        c = ContFuture(trade_symbol, exchange=exchange, currency="USD")
        got = self.ib.qualifyContracts(c)
        if not got:
            raise RuntimeError(f"cannot qualify ContFuture {trade_symbol}@{exchange}")
        front = got[0]
        if min_days_to_expiry <= 0:
            return front
        if self._days_to_expiry(front) >= min_days_to_expiry:
            return front
        return self._next_tradable(trade_symbol, exchange, front,
                                   min_days_to_expiry)

    @staticmethod
    def _days_to_expiry(contract) -> float:
        """Calendar days from now to the contract's last trade date.

        Returns +inf when the broker gives no parseable date so an
        unreadable field can never silently disqualify a contract.
        """
        raw = getattr(contract, "lastTradeDateOrContractMonth", "") or ""
        try:
            exp = pd.Timestamp(raw[:8], tz="UTC")
        except ValueError:
            return float("inf")
        # an empty/garbage field parses to NaT rather than raising, and a NaN
        # day-count would compare False against every threshold — i.e. it
        # would silently disqualify the contract instead of admitting it
        if pd.isna(exp):
            return float("inf")
        return (exp - pd.Timestamp.now(tz="UTC")).total_seconds() / 86400.0

    def _next_tradable(self, trade_symbol: str, exchange: str, front,
                       min_days: int):
        """Nearest listed month at least min_days from expiry."""
        from ib_insync import Future
        details = self.ib.reqContractDetails(
            Future(trade_symbol, exchange=exchange, currency="USD"))
        candidates = sorted(
            (d.contract for d in details or []
             if self._days_to_expiry(d.contract) >= min_days),
            key=self._days_to_expiry)
        if not candidates:
            raise RuntimeError(
                f"no {trade_symbol}@{exchange} contract is at least "
                f"{min_days}d from expiry (front "
                f"{front.localSymbol} is "
                f"{self._days_to_expiry(front):.0f}d)")
        picked = candidates[0]
        if self.dlog:
            self.dlog.log("bars", venue="ibkr", event="expiry_guard_roll",
                          trade_symbol=trade_symbol,
                          front=front.localSymbol,
                          front_days=round(self._days_to_expiry(front), 1),
                          picked=picked.localSymbol,
                          picked_days=round(self._days_to_expiry(picked), 1),
                          min_days=min_days)
        return picked

    def hourly_bars(self, contract, duration: str = "3 D",
                    bar_size: str = "1 hour") -> pd.DataFrame:
        import time as _t
        t0 = _t.monotonic()
        bars = self.ib.reqHistoricalData(
            contract, endDateTime="", durationStr=duration,
            barSizeSetting=bar_size, whatToShow="TRADES", useRTH=False,
            formatDate=2)
        if self.dlog:
            self.dlog.log("bars", venue="ibkr",
                          local_symbol=contract.localSymbol,
                          duration=duration, bar_size=bar_size,
                          rows=len(bars or []),
                          latency_ms=round((_t.monotonic() - t0) * 1000),
                          first=str(bars[0].date) if bars else None,
                          last=str(bars[-1].date) if bars else None)
        if not bars:
            return pd.DataFrame()
        df = pd.DataFrame([{
            "ts": pd.Timestamp(b.date),
            "open": b.open, "high": b.high, "low": b.low, "close": b.close,
            "volume": float(b.volume),
        } for b in bars]).set_index("ts")
        if df.index.tz is None:
            df.index = df.index.tz_localize("UTC")
        else:
            df.index = df.index.tz_convert("UTC")
        return df
