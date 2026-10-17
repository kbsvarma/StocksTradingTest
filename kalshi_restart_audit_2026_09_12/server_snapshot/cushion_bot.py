#!/usr/bin/env python3
"""Kalshi 15-min crypto — CUSHION lane. 1 contract. Restart of the crypto
lane authorised by user 2026-08-08 with the z-filter design.

Rule (validated on 26,135 archive markets + frozen Aug 1-7 holdout,
corrected T-60 spot join, fee-exact, taker):
  Assets BTC/ETH/SOL/XRP 15M. In the final 3-10 minutes, favourite ask in
  [0.85, 0.93), spread <= 2c, and CUSHION z >= 1.5 where
      z = (signed % distance of spot from strike, favourite's direction)
          / (sigma_1m * sqrt(minutes_remaining)),
  sigma_1m = stdev of trailing 60 one-minute log returns (Coinbase spot).
  -> buy 5 YES-equivalent at the ask (IOC taker). One entry per market.
  One position per asset (structural max 4 concurrent). A reduce-only IOC
  disaster stop applies to BTC/ETH/SOL when the executable bid reaches 40c;
  XRP remains excluded by the recovery study.

    archive n=1762 1656-106 +2.32c taker / +1.38c +1c-slip, boot5% +0.18
    holdout n= 246  229-17  +1.72c taker / +0.78c +1c-slip (7d, floor wide)
    ~25-35 signals/day, ~2 losses/day IS THE DESIGN (91c entries carry a
    9.9% loss budget, measured 6-7%).

WHY THIS SHAPE. The unbroken-path filter the old bot used was a proxy for
this variable and failed at it: 19 of the old bot's 20 live losses had
z < 1 at entry (12 had z < 0 — spot already through the strike). The old
93-99c band does NOT survive slippage with any z-cut; 85-93 with z>=1.5
does. The market prices the QUOTE; this lane checks the DISTANCE.

RISK. Correlated flash moves can still hit all four assets in one interval
(Jul 29: three simultaneous). Sizing is the control: worst same-interval
hit at 1ct = ~$3.64. Daily loss halt $10 gates the NEXT day.

Run:   python3 cushion_bot.py --live
Stop:  touch state/HALT_CUSHION
"""
from __future__ import annotations

import argparse
import json
import logging
import math
import os
import statistics
import sys
import time
import urllib.request
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from kalshi_client import Credentials, KalshiAPIError, KalshiClientV2, _sign  # noqa: E402
from ws_feeds import CoinbaseWS, KalshiOrderbookWS, KalshiWS  # noqa: E402

ET = ZoneInfo("America/New_York")
HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "state")
LEDGER = os.path.join(STATE, "cushion_trades.jsonl")
HALT_FILE = os.path.join(STATE, "HALT_CUSHION")
SCAN_FILE = os.path.join(STATE, "scan.json")
STOP_STATE_FILE = os.path.join(STATE, "stop_state.json")
LOG = logging.getLogger("cushion")

SERIES = {"KXBTC15M": "BTC-USD", "KXETH15M": "ETH-USD",
          "KXSOL15M": "SOL-USD", "KXXRP15M": "XRP-USD"}
BAND_LO, BAND_HI = 0.85, 0.93     # favourite ASK band
Z_MIN = 1.5
REM_LO, REM_HI = 3, 10            # minutes remaining, inclusive
MAX_SPREAD = 0.02
CONTRACTS = 5.0                   # 1.0 -> 2.0 (2026-08-09) -> 5.0
                                  # (2026-08-10), all user sizing
                                  # orders. Peak 5 x 4 assets x ~93c
                                  # = ~$18.60 concurrent exposure.
MAX_CONCURRENT = 4                # structural: one per asset
DAILY_LOSS_HALT = None            # NO automatic daily halt (user order
                                  # 2026-08-10 21:3x: "dont have any halts
                                  # on the crypto lane ... let the bot
                                  # ride"). The lane runs through any
                                  # single-day drawdown.
                                  # MANUAL KILL SWITCH IS STILL LIVE:
                                  #   touch cushion/state/HALT_CUSHION
                                  # stops entries immediately.
POLL_S = 2                        # REST fallback cadence when the WS is stale
TICK_S = 0.3                      # hot-loop cadence on websocket quotes
DISCOVER_S = 20                   # REST discovery of new markets/strikes
WS_STALE = 10.0                   # ws silence before falling back to REST
BOOK_REFRESH_S = 1.0              # one batched executable-book snapshot for
                                  # every watched market, not one summary read
                                  # per ticker. Four to eight books cost one
                                  # request and share one receive timestamp.
BOOK_MAX_AGE = 2.5                # fail closed if two consecutive snapshots
                                  # are missed; never timestamp stale summary
                                  # content as fresh executable liquidity
MAX_ATTEMPTS = 3                  # unfilled IOCs per market before giving up
MAX_CROSS = 0.01                  # cross up to signal ask + 1c — this is the
                                  # EXACT stressed-backtest price (+0.78c
                                  # holdout AT ask+1c), not a new rule
SIGMA_MIN_OBS = 30                # refuse to trade on a cold estimator
STOP_ON = False                   # OFF 2026-08-12 01:35 (user order).
                                  # Re-priced on the REAL book it was
                                  # worth +$3.04/75d/1ct (~$0.20/day at
                                  # 5ct), NOT the +$111 claimed: the fill
                                  # 60s after arming is a median 0.050 and
                                  # the book is gone 40.6% of the time.
                                  # Two live defects in 24h. And the ENTRY
                                  # signal is 89.6% vs a 91.4% breakeven,
                                  # which no exit rule can repair.
                                  # Was: 40c disaster stop (user build-go
                                  # 2026-08-11). Kill with STOP_ON=False.
STOP_CONFIRM_S = 60.0             # HOLD-1-BAR: the bid must still be
                                  # under the trigger this many seconds
                                  # after the first touch. Firing on the
                                  # first touch (what ran 08-11 03:44 to
                                  # ~15:00) catches intra-bar WICKS the
                                  # backtest never saw: false-exit rate
                                  # 36.0% vs 10.2%, and value at a 0.38c
                                  # fill -$0.32 vs +$27.73 over 75 days.
                                  # 51 of the 64 positions the first-touch
                                  # trigger stops but this one skips were
                                  # WINNERS.
STOP_DEADZONE_S = 5.0             # skip ONLY inside the last few seconds —
                                  # just enough to submit and fill. The old
                                  # 30s guard plus the 60s confirm created a
                                  # 90-SECOND HOLE at the end of every window
                                  # where the stop could never fire, and that
                                  # is exactly where collapses cluster
                                  # (KXBTC15M-26AUG112230-30, -$4.64).
                                  # Its premise was wrong too: recovery from
                                  # 40c is 18.9%, so a collapsed position is
                                  # worth ~19c while the bid shows 38c.
STOP_TRIGGER = 0.40               # our-side BID at/below this = exit.
                                  # Breakeven recovery = the price you
                                  # sell at; measured recovery on
                                  # BTC/ETH/SOL is 19.7% -> 20pp margin.
STOP_FLOOR = 0.02                 # NOT an economic floor — an empty-book
                                  # guard. ONCE WE DECIDE TO SELL, WE SELL
                                  # (user, 2026-08-11).
                                  # A 22.1% recovery measured AT THE 40c
                                  # TRIGGER does not apply at 25c. The
                                  # decision needs P(win | bid is NOW at X):
                                  # 40c->16.0%, 30c->4.5%, 25c->0/21,
                                  # 20c->0/16, 10c->0/13 (BTC/ETH/SOL,
                                  # >30s left). Holding at 25c is worth
                                  # ZERO while selling nets ~24c.
                                  # MEASURED: 11 of 25 trigger events had
                                  # their first sub-40c bid ALREADY under
                                  # 25c — the bid GAPS, it does not walk —
                                  # and ALL ELEVEN LOST. A 25c floor
                                  # disables the stop 44% of the time,
                                  # exactly during the fastest collapses.
STOP_CROSS = 0.15                 # price this far THROUGH the best bid.
                                  # Used only when depth is unavailable. With
                                  # a live depth snapshot the limit is the
                                  # shallowest level covering the position,
                                  # never a blind fixed-width sweep.
STOP_MAX_TRIES = 6                # re-read the book and re-send for any
                                  # remainder. 40c -> 0 in 15c steps needs
                                  # ~3; 6 leaves margin so the exit
                                  # COMPLETES rather than stranding size.
PAUSED_SERIES = ()                # XRP UNPAUSED 2026-08-12 01:35 with the
                                  # stop. Its pause existed ONLY because it
                                  # was the lone STOP_EXCLUDE entry, making
                                  # the lane a MIXED design; with no stop
                                  # anywhere all 4 series are uniform again.
                                  # INVARIANT STILL HOLDS: anything added to
                                  # STOP_EXCLUDE must also be paused — if the
                                  # stop is ever switched back on, XRP must
                                  # be re-paused in the same edit.
                                  # Was: NO NEW ENTRIES for these (user
                                  # 2026-08-11). Open positions still
                                  # settle; the stop still protects
                                  # anything already held. Resume by
                                  # emptying this tuple.
STOP_EXCLUDE = ("KXXRP15M",)      # 41.4% recovery vs a 40% breakeven —
                                  # stopping XRP LOSES money.
REST_MAX = 0.96                   # maker-leg pilot (user 2026-08-10):
                                  # a RESTING bid may stand up to 96c,
                                  # post_only so it can never take. The
                                  # taker-killed 93-99 study was priced
                                  # on fees+crossing; maker pays neither
                                  # — an open cell, piloted at 1ct.
REST_MAX_CT = 1.0                 # pilot size for the upgraded leg
FLICKER_LOCK_S = 10               # after a below-band weakened skip, lock the
                                  # ticker out briefly: on 2026-08-09 the book
                                  # flickered 91->83->92->83 inside 1.5s and a
                                  # fresh signal 400ms after the weakened skip
                                  # sent an IOC that filled at 83c — below the
                                  # validated band. No Kalshi order type can
                                  # refuse a better price, so the only way to
                                  # keep fills in-band is to not fire into a
                                  # book that just printed below it.
ATTEMPTS_LOG = os.path.join(STATE, "cushion_attempts.jsonl")
RESTING_FILE = os.path.join(os.path.dirname(ATTEMPTS_LOG), "resting.json")
                                  # resting-order registry, persisted on
                                  # EVERY mutation so a restart re-adopts
                                  # live orders instead of canceling them
                                  # (audit 2026-08-10, item 4)


def et_day(ts=None):
    dt = datetime.fromtimestamp(ts, ET) if ts else datetime.now(ET)
    return dt.strftime("%Y-%m-%d")


def fee(p):
    return 0.07 * p * (1 - p)


def ts_of(s):
    try:
        return int(datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp())
    except (ValueError, TypeError):
        return None


def fnum(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def executable_quote_from_book(book):
    """Return executable (YES bid, YES ask) from Kalshi's bid-only book.

    A NO bid at p is a YES ask at 1-p. Both sides must have real depth; market
    summary fields are deliberately excluded because they can lag or remain
    populated when the executable book has already moved or emptied.
    """
    book = book or {}
    yes = (book.get("yes_dollars") or book.get("yes_dollars_fp")
           or book.get("yes") or [])
    no = (book.get("no_dollars") or book.get("no_dollars_fp")
          or book.get("no") or [])

    def _price(level):
        value = float(level[0])
        return value / 100.0 if value > 1.0 else value

    try:
        yes_bid = max(_price(level) for level in yes if float(level[1]) > 0)
        no_bid = max(_price(level) for level in no if float(level[1]) > 0)
    except (TypeError, ValueError, IndexError):
        return None
    yes_ask = 1.0 - no_bid
    if not (0 < yes_bid <= yes_ask < 1):
        return None
    return yes_bid, yes_ask


def cushion_z(spot, strike, side, sigma, rem):
    """Signed distance of spot from strike in the favourite's direction,
    in units of the expected move over the remaining minutes."""
    if not spot or not strike or not sigma or sigma <= 0 or rem <= 0:
        return None
    d = (spot - strike) / strike
    if side == "no":
        d = -d
    return d / (sigma * math.sqrt(rem))


def gates(fb, fa, rem, z):
    if not (REM_LO <= rem <= REM_HI):
        return "rem outside 3-10m"
    if not (BAND_LO <= fa < BAND_HI):
        return "ask outside 85-93c"
    if fa - fb > MAX_SPREAD + 1e-9:
        return "spread > 2c"
    if z is None:
        return "no cushion estimate"
    if z < Z_MIN:
        return f"z {z:.2f} < 1.5"
    return None


class SpotFeed:
    """Coinbase 1-min closes per product; sigma over trailing 60 returns.
    Startup backfills 90 minutes so a restart is never blind."""

    def __init__(self):
        self.bars = {p: {} for p in SERIES.values()}    # product -> {min_ts: close}

    def _fetch(self, product, start, end):
        u = (f"https://api.exchange.coinbase.com/products/{product}/candles"
             f"?granularity=60&start={int(start)}&end={int(end)}")
        rq = urllib.request.Request(u, headers={"User-Agent": "cushion/1.0"})
        with urllib.request.urlopen(rq, timeout=10) as f:
            return json.load(f)

    def backfill(self):
        now = time.time()
        for product in SERIES.values():
            try:
                for r in self._fetch(product, now - 95 * 60, now):
                    self.bars[product][int(r[0])] = float(r[4])
            except Exception as e:                          # noqa: BLE001
                LOG.warning("backfill failed %s: %s", product, e)
        LOG.info("spot backfill: %s",
                 {p.split("-")[0]: len(v) for p, v in self.bars.items()})

    def refresh(self):
        now = time.time()
        for product in SERIES.values():
            try:
                for r in self._fetch(product, now - 4 * 60, now):
                    self.bars[product][int(r[0])] = float(r[4])
            except Exception:                               # noqa: BLE001
                pass
            cut = now - 2 * 3600
            self.bars[product] = {t: v for t, v in self.bars[product].items()
                                  if t >= cut}

    def ingest_completed(self, product, bars):
        """Merge websocket-built bars that are already closed."""
        self.bars.setdefault(product, {}).update(bars)

    def state(self, product, now=None):
        """(latest completed close, sigma of trailing 60 1-min log rets)"""
        bars = self.bars.get(product) or {}
        if len(bars) < SIGMA_MIN_OBS + 2:
            return None, None
        now = time.time() if now is None else now
        # Coinbase keys candles by their START. Its REST response includes
        # the current, still-forming bucket, so the newest raw key is not a
        # completed close. The validated rule is explicitly T-60.
        completed_cutoff = int(now // 60) * 60 - 60
        keys = sorted(key for key in bars if key <= completed_cutoff)
        if len(keys) < SIGMA_MIN_OBS + 2:
            return None, None
        last = keys[-1]
        rets = []
        for i in range(max(1, len(keys) - 61), len(keys)):
            if keys[i] - keys[i-1] == 60:
                rets.append(math.log(bars[keys[i]] / bars[keys[i-1]]))
        if len(rets) < SIGMA_MIN_OBS:
            return bars[last], None
        return bars[last], statistics.pstdev(rets)


class CushionBot:
    def __init__(self, live):
        self.live = live
        self.client = KalshiClientV2(creds=Credentials.load(), dry_run=not live)
        self.spot = SpotFeed()
        self.entered = set()
        self.attempts = {}       # ticker -> unfilled IOC attempts
        self.flicker_block = {}  # ticker -> unix ts until which entries wait
        self.k_src = {}          # ticker -> strike source: official|computed
        self.stopped_out = set()
        self._stop_armed = {}     # ticker -> ts of the FIRST touch below
                                  # the trigger. Cleared the moment the bid
                                  # recovers, so only a SUSTAINED move
                                  # fires (hold-1-bar).
        self.stop_pending = {}    # ticker -> submitted stop order awaiting
                                  # confirmation; persisted before submit
        self._pos_cache = ({}, 0.0)
        self._stop_tries = {}     # ticker -> attempts so far
        self._fb_done = set()    # oid2s whose fills are already booked —
                                 # _finalize_fallback can be reached twice
                                 # for one order (terminal path + finalize
                                 # probe); a row per FILL, never per call
        self.resting = {}        # ticker -> live resting-order state (GTC)
        self.halted = ""
        self.meta = {}           # tkr -> (series, strike, close_ts)
        self.book_quotes = {}    # tkr -> executable (YES bid, YES ask, recv_ts)
        self.last_book_refresh = 0.0
        self.last_book_error_log = 0.0
        self.last_discover = 0.0
        self.last_scan_write = 0.0
        os.makedirs(STATE, exist_ok=True)
        self._load_stop_state()
        for row in self.trades():
            self.entered.add(row.get("ticker"))
        self._startup_adopt()
        self.spot.backfill()
        self.kws = KalshiWS(self.client.creds, _sign, fill_channel=True,
                            order_channel=True)
        self.kws.start()
        self.ows = KalshiOrderbookWS(self.client.creds, _sign,
                                     wake=self.kws.wake)
        self.ows.start()
        self.cws = CoinbaseWS(list(SERIES.values()))
        self.cws.start()

    def trades(self):
        if not os.path.exists(LEDGER):
            return []
        out = []
        with open(LEDGER) as ledger:
            for line in ledger:
                try:
                    out.append(json.loads(line))
                except Exception:                           # noqa: BLE001
                    pass
        return out

    def log_trade(self, rec):
        with open(LEDGER, "a") as f:
            f.write(json.dumps(rec) + "\n")

    def open_positions(self):
        return [t for t in self.trades() if t.get("result") == "open"]

    def stop_stats(self):
        """Live scoreboard for the stop: how often it fired and how
        often it cut something that would have won."""
        rows = [t for t in self.trades() if t.get("exit_ct")]
        done = [t for t in rows if t.get("would_have")]
        return (len(rows), sum(1 for t in done if t["would_have"] == "win"),
                len(done))

    def realised_today(self):
        d = et_day()
        return sum(t.get("pnl", 0.0) for t in self.trades()
                   if t.get("result") in ("win", "loss", "stopped")
                   and t.get("settled_day") == d)

    def check_halt(self):
        new = ""
        if os.path.exists(HALT_FILE):
            new = "HALT file"
        elif (DAILY_LOSS_HALT is not None
                and self.realised_today() <= -abs(DAILY_LOSS_HALT)):
            new = "daily loss halt"
        if new != self.halted:
            LOG.warning("HALT -> %r", new or "cleared")
        self.halted = new
        return bool(new)

    def positions(self):
        try:
            r = self.client._request("GET", "/portfolio/positions",
                                     params={"limit": 200})
        except KalshiAPIError:
            return {}
        out = {}
        for p in (r or {}).get("market_positions", []) or []:
            q = fnum(p.get("position_fp")) or fnum(p.get("position")) or 0.0
            if q:
                out[p.get("ticker")] = q
        return out

    def _save_resting(self):
        """Persist the resting registry on EVERY mutation (audit item 4).
        This file is what lets a restart tell a valid in-band rest from a
        crash orphan: without it the 2026-08-10 12:21 deploy restart had
        no way to know and canceled two live 12:30-window orders."""
        try:
            tmp = RESTING_FILE + ".tmp"
            with open(tmp, "w") as f:
                json.dump(self.resting, f)
            os.replace(tmp, RESTING_FILE)   # atomic: a crash mid-write must
                                            # never leave a torn registry for
                                            # the next startup to trust
        except Exception:                                   # noqa: BLE001
            pass

    def _load_resting(self):
        try:
            with open(RESTING_FILE) as f:
                d = json.load(f) or {}
            return {t: r for t, r in d.items() if isinstance(r, dict)}
        except Exception:                                   # noqa: BLE001
            return {}

    def _startup_adopt(self):
        """Crash-window reconcile (2026-08-09): a fill followed by a crash
        before the ledger write leaves a live position the restart cannot
        see (entered rebuilds from the ledger only). Scan recent attempts;
        adopt any exchange position that has no ledger row."""
        # RESTART RE-ADOPTION (audit 2026-08-10, item 4). A restart is
        # not a reason to abandon a valid order. Re-adopt what the
        # persisted registry accounts for; cancel only true strangers.
        # Registry entries whose orders vanished while we were down are
        # pushed through the terminal finalizer so any fills they took
        # reach the ledger (never re-risked either way).
        persisted = self._load_resting()
        known = {}
        for t, r in persisted.items():
            if r.get("oid"):
                known[r["oid"]] = t
            if r.get("oid2"):
                known[r["oid2"]] = t
        live_oids = set()
        try:
            d = self.client._request("GET", "/portfolio/orders",
                                     params={"status": "resting",
                                             "limit": 100})
            for o in (d or {}).get("orders", []) or []:
                ot, oo = o.get("ticker", ""), o.get("order_id")
                if not any(ot.startswith(s) for s in SERIES):
                    continue
                if oo in known:
                    live_oids.add(oo)
                    continue
                try:
                    self.client.cancel_order_v2(oo)
                    LOG.warning("STARTUP: canceled UNKNOWN orphan resting "
                                "order on %s (not in persisted registry)",
                                ot)
                except KalshiAPIError:
                    pass
        except KalshiAPIError:
            pass
        for t, r in persisted.items():
            if t in self.entered:
                continue
            if r.get("oid") in live_oids or r.get("oid2") in live_oids:
                r["last_poll"] = 0.0
                if r.get("oid2") and r["oid2"] not in live_oids:
                    # AUDIT ROUND-2 (2026-08-10): "gone" can mean FILLED
                    # while we were down, not just canceled/expired —
                    # reconcile its fills into the ledger BEFORE dropping
                    # the id, or a filled 1ct maker96 leg disappears from
                    # the books while the primary keeps resting.
                    self._finalize_fallback(t, r)
                    r["oid2"] = None
                self.resting[t] = r
                LOG.warning("STARTUP RE-ADOPT %s: resting order alive on "
                            "the exchange — watchdog resumed (limit %.3f, "
                            "z0 %.2f)", t, r.get("limit") or 0,
                            r.get("z0") or 0)
            else:
                self.resting[t] = r     # finalizer reads limit/oid2 from it
                if not self._finalize_fill(
                        t, r.get("series", t.split("-")[0]),
                        r.get("side", "yes"), r.get("sig", 0), 0, None,
                        r.get("oid"), terminal=True):
                    self.resting.pop(t, None)
                if r.get("oid2"):
                    self._finalize_fallback(t, r)
        self._save_resting()
        try:
            cutoff = time.time() - 1800
            tkrs = set()
            if os.path.exists(ATTEMPTS_LOG):
                with open(ATTEMPTS_LOG, "rb") as f:
                    f.seek(max(0, os.path.getsize(ATTEMPTS_LOG) - 200_000))
                    for line in f.read().decode(errors="ignore").splitlines():
                        try:
                            r = json.loads(line)
                        except ValueError:
                            continue
                        if r.get("ts", 0) > cutoff:
                            tkrs.add(r.get("ticker"))
            # CRASH-SAFETY SWEEP (audit round 2): a crash between order
            # submit and the attempt-row write leaves no attempts trace —
            # sweep every series position so a filled crash-window order
            # is still adopted. The exchange is the source of truth.
            try:
                pr = self.client._request("GET", "/portfolio/positions",
                                          params={"limit": 200})
                for p in (pr or {}).get("market_positions", []) or []:
                    q = (fnum(p.get("position_fp"))
                         or fnum(p.get("position")) or 0.0)
                    pt = p.get("ticker") or ""
                    if q and any(pt.startswith(s) for s in SERIES):
                        tkrs.add(pt)
            except KalshiAPIError:
                pass
            for t in tkrs - self.entered:
                if not t:
                    continue
                pos = abs(self.position_of(t) or 0.0)
                if pos > 1e-9:
                    self.entered.add(t)
                    LOG.warning("STARTUP ADOPT %s: live position %.2f with "
                                "no ledger row — crash-window orphan", t, pos)
                    self.log_trade({
                        "ts": int(time.time()), "ticker": t,
                        "series": t.split("-")[0], "side": "yes",
                        "entry": 0.0, "count": round(pos, 4), "rem": 0,
                        "z": 0, "signal_ask": 0,
                        "result": "open", "lane": "cushion",
                        "adopted": True, "orphan": True,
                    })
        except Exception as e:                          # noqa: BLE001
            LOG.warning("startup adopt reconcile failed: %s", e)

    def _save_stop_state(self):
        """Write-ahead stop registry. A restart must reconcile an ambiguous
        submission before it is allowed to send another close order."""
        try:
            tmp = STOP_STATE_FILE + ".tmp"
            with open(tmp, "w") as f:
                json.dump({"pending": self.stop_pending,
                           "tries": self._stop_tries,
                           "armed": self._stop_armed}, f)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, STOP_STATE_FILE)
        except Exception as e:                              # noqa: BLE001
            LOG.error("stop state write failed: %s", e)
            raise

    def _load_stop_state(self):
        try:
            with open(STOP_STATE_FILE) as f:
                data = json.load(f) or {}
            self.stop_pending = {
                str(t): p for t, p in (data.get("pending") or {}).items()
                if isinstance(p, dict)
            }
            self._stop_tries = {
                str(t): int(n) for t, n in (data.get("tries") or {}).items()
            }
            # armed timestamps survive a restart, so a confirm in progress
            # is not silently reset to zero and delayed another 60s
            self._stop_armed = {
                str(t): float(v)
                for t, v in (data.get("armed") or {}).items()
                if isinstance(v, (int, float))
            }
        except FileNotFoundError:
            return
        except Exception as e:                              # noqa: BLE001
            raise RuntimeError(f"stop state unreadable: {e}") from e

    def _write_ledger_rows(self, rows):
        tmp = LEDGER + ".tmp"
        with open(tmp, "w") as f:
            for row in rows:
                f.write(json.dumps(row) + "\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, LEDGER)

    def _ledger_remaining(self, tkr):
        return round(sum(max(0.0, float(r.get("count") or 0.0)
                                 - float(r.get("exit_ct") or 0.0))
                         for r in self.trades()
                         if r.get("ticker") == tkr
                         and r.get("result") == "open"), 4)

    def _record_stop_fill(self, tkr, order_key, cumulative_ct, favorite_px,
                          trigger_bid, now):
        """Idempotently allocate an order's cumulative fills across every
        open ledger leg for the ticker and persist the same modified rows.

        ``order_key`` is the write-ahead client id, so replay after a crash
        cannot book the same exchange fill twice.
        """
        rows = self.trades()
        already = 0.0
        for row in rows:
            if row.get("ticker") == tkr:
                already += float((row.get("exit_orders") or {})
                                .get(order_key, 0.0))
        delta = max(0.0, float(cumulative_ct) - already)
        if delta <= 1e-9:
            return 0.0
        left = delta
        for row in rows:
            if row.get("ticker") != tkr or row.get("result") != "open":
                continue
            old_ct = float(row.get("exit_ct") or 0.0)
            capacity = max(0.0, float(row.get("count") or 0.0) - old_ct)
            take = min(capacity, left)
            if take <= 1e-9:
                continue
            old_px = float(row.get("exit_px") or 0.0)
            new_ct = old_ct + take
            row["exit_ct"] = round(new_ct, 4)
            row["exit_px"] = round(
                (old_px * old_ct + favorite_px * take) / new_ct, 4)
            row["exit_ts"] = int(now)
            row["stop_bid"] = round(trigger_bid, 4)
            orders = dict(row.get("exit_orders") or {})
            orders[order_key] = round(float(orders.get(order_key, 0.0))
                                      + take, 4)
            row["exit_orders"] = orders
            left -= take
            if left <= 1e-9:
                break
        self._write_ledger_rows(rows)
        if left > 1e-9:
            LOG.error("STOP %s: exchange filled %.2f more than open ledger "
                      "capacity; %.2f unallocated", tkr, delta, left)
        return delta - left

    def our_bid(self, tkr, side):
        """Best bid ON OUR SIDE from the executable order book — the
        price we could actually SELL into. Streamed book first (the
        same feed entries use), REST as the fail-safe. Returns None if
        there is no book, which must NOT be read as a low price."""
        ows = getattr(self, "ows", None)
        levels = (ows.outcome_bids(tkr, side)
                  if ows and hasattr(ows, "outcome_bids") else None)
        if levels:
            return levels[0][0][0]
        q = ows.quote(tkr) if ows else None
        if q is not None:
            yes_bid, yes_ask = q[0], q[1]
            return yes_bid if side == "yes" else 1.0 - yes_ask
        try:
            d = self.client._request("GET", f"/markets/{tkr}/orderbook",
                                     params={"depth": 1})
        except KalshiAPIError:
            return None
        book = ((d or {}).get("orderbook_fp")
                or (d or {}).get("orderbook") or {})
        yes = book.get("yes_dollars") or book.get("yes") or []
        no = book.get("no_dollars") or book.get("no") or []

        def _px(p):
            v = float(p)
            return v / 100.0 if v > 1.0 else v
        try:
            if side == "yes":
                return max(_px(p) for p, _ in yes) if yes else None
            return max(_px(p) for p, _ in no) if no else None
        except (TypeError, ValueError):
            return None

    def _stop_limit(self, tkr, side, size, observed_bid):
        """Shallowest live level expected to cover ``size`` contracts.

        This receives normal price improvement while refusing to authorize
        a deeper sweep than the current depth snapshot actually requires.
        """
        ows = getattr(self, "ows", None)
        snap = (ows.outcome_bids(tkr, side)
                if ows and hasattr(ows, "outcome_bids") else None)
        if snap:
            cumulative = 0.0
            for price, quantity in snap[0]:
                if price < STOP_FLOOR - 1e-9:
                    break
                cumulative += quantity
                if cumulative >= size - 1e-9:
                    return round(price, 4), round(cumulative, 4)
            return STOP_FLOOR, round(cumulative, 4)
        return max(STOP_FLOOR,
                   round(observed_bid - STOP_CROSS, 4)), None

    def manage_stops(self, now):
        """Disaster stop. One pass over the positions this bot opened:
        if our-side bid has fallen to STOP_TRIGGER, sell out."""
        if not STOP_ON:
            return
        # Pending IOC results are reconciled before any new submission.
        self._check_stop_fills(now)
        rows = [r for r in self.trades() if r.get("result") == "open"]
        tickers = []
        for row in rows:
            tkr = row.get("ticker")
            if tkr and tkr not in tickers and self._ledger_remaining(tkr) > 0:
                tickers.append(tkr)
        for tkr in tickers:
            trows = [r for r in rows if r.get("ticker") == tkr]
            sides = {r.get("side") for r in trows if r.get("side")}
            if len(sides) != 1:
                LOG.error("STOP %s refused: inconsistent ledger sides %s",
                          tkr, sorted(sides))
                continue
            if any(tkr.startswith(s) for s in STOP_EXCLUDE):
                continue
            meta = self.meta.get(tkr)
            close_ts = meta[2] if meta else None
            if close_ts and close_ts - now <= 1.0:
                continue        # genuinely too late to submit anything
            if tkr in self.stop_pending:
                continue        # already submitted; confirmed below
            side = next(iter(sides))
            bid = self.our_bid(tkr, side)
            if bid is None:
                continue

            armed = self._stop_armed.get(tkr)
            if armed is None:
                if bid > STOP_TRIGGER + 1e-9:
                    continue
                self._stop_armed[tkr] = now
                self._save_stop_state()
                LOG.warning("STOP ARMED %s at bid %.3f — deciding in %.0fs "
                            "(or sooner if the close forces it)", tkr, bid,
                            STOP_CONFIRM_S)
                continue

            # HOLD-1-BAR, implemented FAITHFULLY. The arm stands and ONE
            # check at the confirmation instant decides. Intermediate ticks
            # are ignored: the backtest compared the bid at two points 60s
            # apart and never saw what happened between them. Disarming on
            # any tick above the trigger is a DIFFERENT, untested rule, and
            # on a flickering book it never fires at all — the book that
            # cost us KXBTC15M-26AUG112230-30 crossed 0.40 six times in
            # 1.5s and reset the clock every time.
            deadline = armed + STOP_CONFIRM_S
            if close_ts:
                # the decision must never expire INSIDE the dead zone; if
                # that means confirming for less than STOP_CONFIRM_S, so be
                # it — a short confirmation beats a guaranteed full loss.
                deadline = min(deadline, close_ts - STOP_DEADZONE_S)
            if now < deadline:
                continue
            if bid > STOP_TRIGGER + 1e-9:
                self._stop_armed.pop(tkr, None)
                self._save_stop_state()
                LOG.info("STOP disarmed %s — bid back at %.3f after %.0fs",
                         tkr, bid, now - armed)
                continue
            # FIRE EVERY TRIGGERED POSITION IN THIS TICK. Correlated
            # collapses hit 3 assets at once (2026-08-10: three times in
            # one evening); making the 3rd wait a tick per exit is how a
            # 40c stop becomes a 5c stop.
            self._do_stop(tkr, side, bid, now)
            self._stop_armed.pop(tkr, None)

    def _do_stop(self, tkr, side, bid, now):
        """Sell the position. Kalshi matches at the RESTING price, so we
        price STOP_CROSS through the book to guarantee the cross while
        the limit caps the worst fill at STOP_FLOOR. Retry the remainder
        with a FRESH book read — thin books fill in pieces."""
        held = self._ledger_remaining(tkr)
        if held < 1e-9:
            self.stopped_out.add(tkr)
            return
        tries = self._stop_tries.get(tkr, 0)
        if tries >= STOP_MAX_TRIES:
            return
        if bid < STOP_FLOOR - 1e-9:
            LOG.warning("STOP %s: bid %.3f below %.2f — no book worth "
                        "selling into, letting it settle", tkr, bid,
                        STOP_FLOOR)
            return
        limit, visible = self._stop_limit(tkr, side, held, bid)
        # exiting NO means buying YES back; exiting YES means selling YES
        oside = "bid" if side == "no" else "ask"
        px = round(1.0 - limit, 4) if side == "no" else round(limit, 4)
        client_id = str(uuid.uuid4())
        sent = time.time()
        pending = {"oid": None, "client_id": client_id, "side": side,
                   "limit": limit, "bid": bid, "size": held,
                   "visible_ct": visible, "sent": sent,
                   "accounted_ct": 0.0}
        self.stop_pending[tkr] = pending
        self._stop_tries[tkr] = tries + 1
        self._save_stop_state()             # write-ahead, before POST
        try:
            resp = self.client.create_order_v2(
                ticker=tkr, side=oside, count=held, price=px,
                client_order_id=client_id,
                time_in_force="immediate_or_cancel", post_only=False,
                reduce_only=True, cancel_order_on_pause=True)
        except KalshiAPIError as e:
            # Submission may have reached the matching engine even when the
            # response did not reach us. Keep the write-ahead intent pending;
            # reconciliation by client id owns the next decision.
            pending["submit_error"] = str(e)
            self._save_stop_state()
            LOG.error("STOP order ambiguous %s: %s — reconciling, no blind "
                      "retry", tkr, e)
            self._log_attempt({"ts": now, "ticker": tkr, "side": side,
                               "outcome": "stop_order_ambiguous",
                               "error": str(e), "bid": round(bid, 4)})
            return
        order = ((resp.get("order") or resp)
                 if isinstance(resp, dict) else {})
        oid = order.get("order_id")
        pending["oid"] = oid
        self._save_stop_state()
        LOG.warning("STOP SUBMITTED %s attempt %d: bid %.3f limit %.3f "
                    "size %.2f visible=%s (rtt %.0fms)", tkr, tries + 1,
                    bid, limit, held, visible,
                    (time.time() - sent) * 1000)
        self._log_attempt({"ts": now, "ticker": tkr, "side": side,
                           "outcome": "stop_submitted",
                           "bid": round(bid, 4), "limit": round(limit, 4),
                           "size": held, "order_id": oid,
                           "client_order_id": client_id,
                           "submit_rtt_ms": round((time.time()-sent)*1000, 1)})
        # V2 IOC responses are final matching-engine results. Account them
        # now instead of waiting for the eventually-consistent fills index.
        raw_count = (order.get("fill_count")
                     if order.get("fill_count") is not None
                     else order.get("fill_count_fp"))
        if raw_count is not None:
            filled = float(raw_count or 0.0)
            avg_yes = fnum(order.get("average_fill_price"))
            favorite_px = ((avg_yes if side == "yes" else 1.0 - avg_yes)
                           if avg_yes is not None else limit)
            if filled > 1e-9:
                self._record_stop_fill(tkr, client_id, filled, favorite_px,
                                       bid, time.time())
                LOG.warning("STOP FILLED %s %.2f/%.2f @ %.3f from IOC "
                            "response", tkr, filled, held, favorite_px)
            self.stop_pending.pop(tkr, None)
            self._save_stop_state()
            self._pos_cache = ({}, 0.0)

    def _check_stop_fills(self, now):
        """Reconcile legacy/ambiguous responses without blind resubmission."""
        for tkr in list(self.stop_pending):
            p = self.stop_pending[tkr]
            if now - p["sent"] < 0.25:
                continue
            oid = p.get("oid")
            order = None
            if oid:
                try:
                    data = self.client._request(
                        "GET", f"/portfolio/orders/{oid}")
                    order = (data or {}).get("order") or data
                except KalshiAPIError:
                    order = None
            else:
                try:
                    data = self.client._request(
                        "GET", "/portfolio/orders",
                        params={"ticker": tkr,
                                "min_ts": int(p["sent"]) - 2,
                                "limit": 100})
                    order = next((o for o in (data or {}).get("orders", [])
                                  if o.get("client_order_id")
                                  == p.get("client_id")), None)
                    if order:
                        oid = order.get("order_id")
                        p["oid"] = oid
                        self._save_stop_state()
                except KalshiAPIError:
                    order = None
            sz = 0.0
            ypx = None
            if oid:
                sz, ypx, _tak = self._fills_for_oid(tkr, oid)
            order_count = None
            if order:
                raw = (order.get("fill_count_fp")
                       if order.get("fill_count_fp") is not None
                       else order.get("fill_count"))
                order_count = fnum(raw)
            cumulative = max(sz, order_count or 0.0)
            side = p["side"]
            if cumulative > 1e-9:
                avg_yes = fnum((order or {}).get("average_fill_price"))
                if ypx is None:
                    ypx = avg_yes
                got = ((ypx if side == "yes" else 1.0 - ypx)
                       if ypx is not None else p["limit"])
                booked = self._record_stop_fill(
                    tkr, p["client_id"], cumulative, got, p["bid"], now)
                p["accounted_ct"] = cumulative
                self._save_stop_state()
                self._log_attempt({"ts": now, "ticker": tkr, "side": side,
                                   "outcome": "stop_filled",
                                   "sold": round(booked, 4),
                                   "fill": round(got, 4),
                                   "of": p["size"]})
                LOG.warning("STOP FILLED %s cumulative %.2f/%.2f @ %.3f "
                            "(bid was %.3f, %.0fms after submit)", tkr,
                            cumulative, p["size"], got, p["bid"],
                            (now - p["sent"]) * 1000)
            status = str((order or {}).get("status") or "").lower()
            final = status in ("canceled", "executed", "filled")
            if final or (order is None and now - p["sent"] >= 2.0):
                if cumulative <= 1e-9:
                    self._log_attempt({"ts": now, "ticker": tkr,
                                       "outcome": "stop_unfilled",
                                       "limit": round(p["limit"], 4)})
                    LOG.warning("STOP UNFILLED %s @ %.3f — fresh retry "
                                "allowed", tkr, p["limit"])
                self.stop_pending.pop(tkr, None)
                self._save_stop_state()
                self._pos_cache = ({}, 0.0)
            elif cumulative <= 1e-9 and now - p["sent"] >= 5.0:
                self._log_attempt({"ts": now, "ticker": tkr,
                                   "outcome": "stop_reconcile_wait",
                                   "client_order_id": p.get("client_id")})

    def book_ask(self, tkr, side):
        """The side ask from the REAL orderbook (depth 1); None if empty.
        Summaries lie on quiet books; resting orders do not."""
        orderbook_ws = getattr(self, "ows", None)
        streamed = orderbook_ws.quote(tkr) if orderbook_ws else None
        if streamed is not None:
            yes_bid, yes_ask = streamed[0], streamed[1]
            return yes_ask if side == "yes" else 1.0 - yes_bid
        try:
            d = self.client._request("GET", f"/markets/{tkr}/orderbook",
                                     params={"depth": 1})
        except KalshiAPIError:
            return None
        # FORMAT FIX 2026-08-10 03:0x: the endpoint returns
        # {"orderbook_fp": {"yes_dollars": [["0.0310","93.00"],...]}} —
        # dollar STRINGS under _fp/_dollars keys. The old parse read
        # nonexistent keys and returned EMPTY for every real book: the
        # phantom guard refused 100% of signals for ~7h. Verified against
        # a raw response; both shapes accepted defensively.
        book = ((d or {}).get("orderbook_fp")
                or (d or {}).get("orderbook") or {})
        quote = executable_quote_from_book(book)
        if quote is None:
            return None
        yes_bid, yes_ask = quote
        return yes_ask if side == "yes" else 1.0 - yes_bid

    def position_of(self, tkr):
        return self.positions().get(tkr, 0.0)

    def confirm_fill(self, tkr, oid, since, window=1.2, pos_fallback=True):
        """(size, yes_price, certain) for THIS order. Sums ALL fill rows for
        the order id — one IOC sweeping several resting counterparties comes
        back as ONE ROW PER COUNTERPARTY (live 2026-08-09: a 2ct order filled
        as 2x1.00 rows; reading only the first row undercounted the fill,
        triggered a spurious top-up, and over-bought the position).
        The aggregate-position fallback is only meaningful when nothing has
        been bought yet this signal — top-up confirms pass pos_fallback=False
        so a lagging fills index can never count the FIRST order's contracts
        as the top-up's."""
        deadline = time.time() + window
        saw = False
        matched_empty = False
        while time.time() < deadline:
            try:
                d = self.client._request("GET", "/portfolio/fills",
                                         params={"ticker": tkr, "limit": 50})
                saw = True
            except KalshiAPIError:
                d = None
            rows = [f for f in (d or {}).get("fills", []) or []
                    if oid and f.get("order_id") == oid]
            if rows:
                sz = pxw = 0.0
                for f in rows:
                    c = fnum(f.get("count_fp")) or fnum(f.get("count")) or 0.0
                    p = fnum(f.get("yes_price_dollars"))
                    sz += c
                    if p is not None:
                        pxw += c * p
                if sz > 0:
                    px = (pxw / sz) if pxw > 0 else None
                    return sz, px, True
                # STAGED-WRITE TRAP (live 2026-08-09 16:06): the fills index
                # shows the row BEFORE its count fields populate. Summing it
                # as 0 and returning certain declared a 2.0 fill "certainly
                # unfilled" in 700ms; the re-signal double-bought (4ct on a
                # 2ct signal). A matched row is proof of ACTIVITY — poll on.
                matched_empty = True
            if pos_fallback and self.position_of(tkr):
                return abs(self.position_of(tkr)), None, True
            time.sleep(0.5)
        # deadline: one last authoritative look before any verdict
        if pos_fallback and self.position_of(tkr):
            return abs(self.position_of(tkr)), None, True
        if matched_empty:
            return 0.0, None, False     # activity seen -> UNKNOWN, no retry
        return 0.0, None, saw

    def take(self, tkr, series, side, ask, rem, z, quote_ts=None):
        if self.attempts.get(tkr, 0) > 0:
            # RETRY of a previously-attempted market: before risking a
            # double, ask the exchange whether the 'missed' order actually
            # filled (2026-08-09: dual-endpoint lag made a real 2.0 fill
            # read as a certain miss; the retry bought 2 more).
            pos = abs(self.position_of(tkr) or 0.0)
            if pos > 1e-9:
                self.entered.add(tkr)
                LOG.warning("ADOPT %s: prior 'missed' attempt actually "
                            "filled %.2f — adopting, no re-entry", tkr, pos)
                self.log_trade({
                    "ts": int(time.time()), "ticker": tkr, "series": series,
                    "side": side, "entry": round(ask, 4), "count": round(pos, 4),
                    "rem": rem, "z": round(z, 2), "signal_ask": ask,
                    "result": "open", "lane": "cushion", "adopted": True,
                })
                return
        if time.time() < self.flicker_block.get(tkr, 0.0):
            self._log_attempt({"ts": int(time.time()), "ticker": tkr,
                               "side": side, "signal_ask": ask,
                               "rem": round(rem, 2), "z": round(z, 2),
                               "outcome": "flicker_lock"})
            return
        # Order submission gets one final authoritative depth-one read. The
        # scanner already uses executable books, but a fast market can move
        # during the few milliseconds between qualification and submission.
        live = self.book_ask(tkr, side)
        if live is None:
            self._log_attempt({"ts": int(time.time()), "ticker": tkr,
                               "side": side, "signal_ask": ask,
                               "rem": round(rem, 2), "z": round(z, 2),
                               "outcome": "phantom_book"})
            LOG.info("skip %s — executable orderbook is EMPTY", tkr)
            self.flicker_block[tkr] = time.time() + 3.0
            return
        submit_started = time.time()
        att = {"ts": submit_started, "ticker": tkr, "side": side,
               "signal_ask": ask, "live_ask": live, "rem": round(rem, 2),
               "z": round(z, 2),
               "signal_ts_ms": (int(quote_ts * 1000)
                                if quote_ts is not None else None),
               "submit_started_ms": int(submit_started * 1000),
               "signal_to_submit_ms": (round(
                   max(0.0, submit_started - quote_ts) * 1000, 3
               ) if quote_ts is not None else None),
               # capture-funnel classification (audit item 7): a signal is
               # DEPTH-VALID when the real book shows an executable ask
               # within what the rule may pay. Fill-rate is judged against
               # these — chasing the rest means paying 96-98c, which is a
               # different (negative) strategy, not a missed fill.
               "depth_valid": bool(
                   live is not None
                   and live <= min(ask + MAX_CROSS,
                                   BAND_HI + MAX_CROSS) + 1e-9)}
        if live is None:
            att["outcome"] = "no_book"
            self._log_attempt(att)
            return
        if live > ask + MAX_CROSS + 1e-9 or live >= BAND_HI + MAX_CROSS:
            # GTC EDITION: a run is no longer a miss — the order still goes
            # on the book at the capped price and catches the flick-back.
            LOG.info("ask ran %.2f -> %.2f on %s — resting at cap", ask, live, tkr)
            att["ran"] = round(live, 3)
        if live < BAND_LO - 1e-9:
            # BUG FIX 2026-08-08: a DROPPING ask is the favourite weakening in
            # real time — the market leaving the validated state, not a
            # bargain. Two live fills printed at 83-84c (below the band floor
            # the backtest never traded); one lost. Signal AND order must both
            # be in-band.
            LOG.info("skip %s — ask weakened %.2f -> %.2f (below band), "
                     "locked %ds", tkr, ask, live, FLICKER_LOCK_S)
            att["outcome"] = "weakened"
            self._log_attempt(att)
            self.flicker_block[tkr] = time.time() + FLICKER_LOCK_S
            return
        limit = min(live + MAX_CROSS, ask + MAX_CROSS)
        oside = "bid" if side == "yes" else "ask"
        px = round(limit if side == "yes" else 1.0 - limit, 4)
        LOG.info("ENTER %s %s rem=%.1fm sig=%.2f live=%.2f limit=%.3f z=%.2f",
                 tkr, side, rem, ask, live, limit, z)
        sent = time.time()
        # SINGLE EXPIRING GTC (user design 2026-08-09). One order per
        # signal, placed the instant the state qualifies. Book present ->
        # crosses like the old IOC. Book ran -> rests at the capped price;
        # every flick-back at <= our price fills a queue-resident order.
        # Safety: manage_resting cancels on z-death; the exchange itself
        # expires the order at rem=REM_LO (crash-proof); fills are tagged
        # maker/taker and fees charged only on the taker portion.
        limit = min(limit, BAND_HI + MAX_CROSS - 0.001)
        px = round(limit if side == "yes" else 1.0 - limit, 4)
        # FINAL ORDER-FUNCTION CEILING (audit 2026-08-10, item 5). The
        # min() above is the binding cap; this guard exists so any future
        # edit that re-prices `limit` after it (the metals WTI 98c fills
        # came from exactly such a repricing that skipped the gate) fails
        # CLOSED here instead of reaching the exchange.
        if limit > BAND_HI + MAX_CROSS - 0.001 + 1e-9:
            att["outcome"] = "final_cap_block"
            att["limit"] = round(limit, 4)
            self._log_attempt(att)
            LOG.error("FINAL CAP BLOCK %s: limit %.3f above ceiling %.3f — "
                      "order refused", tkr, limit,
                      BAND_HI + MAX_CROSS - 0.001)
            return
        cts_close = (self.meta.get(tkr) or (None, None, 0))[2]
        exp_ts = int(cts_close - REM_LO * 60) if cts_close else int(sent + 420)
        try:
            resp = self.client.create_order_v2(
                ticker=tkr, side=oside, count=CONTRACTS, price=px,
                client_order_id=str(uuid.uuid4()),
                time_in_force="good_till_canceled",
                expiration_time=exp_ts, post_only=False)
        except KalshiAPIError as e:
            LOG.error("order failed %s: %s", tkr, e)
            att["outcome"] = "order_error"
            self._log_attempt(att)
            return
        oid = ((resp.get("order") or {}).get("order_id")
               or resp.get("order_id")) if isinstance(resp, dict) else None
        acknowledged = time.time()
        att["ack_ts_ms"] = int(acknowledged * 1000)
        att["submit_rtt_ms"] = round((acknowledged - sent) * 1000, 3)
        if oid is None:
            self.entered.add(tkr)   # cannot manage an unidentified order
            att["outcome"] = "unknown_marked_entered"
            self._log_attempt(att)
            LOG.warning("%s order id missing — marked entered, startup "
                        "reconcile owns it", tkr)
            return
        # NON-BLOCKING SUBMISSION. Register immediately and let the fill and
        # user-order websocket channels wake manage_resting(). Serially
        # waiting one second here delayed sibling assets in correlated moves.
        self.attempts[tkr] = self.attempts.get(tkr, 0) + 1
        # PRIMARY (default, unchanged): the original order keeps resting at
        # the validated capped price, full CONTRACTS size. Flick-backs fill
        # it at 85-93.9c exactly as validated.
        # FALLBACK (pilot): an ADDITIONAL 1ct post_only bid at REST_MAX —
        # fills only if a seller comes down to 96c and the exchange
        # guarantees it can never cross as a taker. Canceled the moment
        # the primary fills (user requirement 2026-08-10: default first,
        # pilot strictly as fallback).
        self.resting[tkr] = {"oid": oid, "side": side, "series": series,
                             "limit": limit, "sig": ask, "z0": z,
                             "placed": sent, "exp": exp_ts,
                             "oid2": None, "last_poll": 0.0,
                             "att": att,
                             "fallback_due": acknowledged + 1.0,
                             "fallback_attempted": False,
                             "queue_checked": False}
        self._save_resting()
        # Keep the established funnel label: "rested" means a GTC was
        # accepted and registered, not that it remained unfilled for a tick.
        att["outcome"] = "rested"
        att["fallback96"] = False
        self._log_attempt(att)
        LOG.info("SUBMITTED %s %s %.0fct @ %.3f primary (sig %.2f, z=%.2f, "
                 "expires rem%d) — confirmation async", tkr, side,
                 CONTRACTS, limit, ask, z, REM_LO)

    def _fills_for_oid(self, tkr, oid):
        """(size, yes_vwap, taker_ct) summed over ALL fill rows of oid."""
        try:
            d = self.client._request("GET", "/portfolio/fills",
                                     params={"ticker": tkr, "limit": 50})
        except KalshiAPIError:
            return 0.0, None, 0.0
        sz = pxw = tak = 0.0
        for f in (d or {}).get("fills", []) or []:
            if f.get("order_id") != oid:
                continue
            c = fnum(f.get("count_fp")) or fnum(f.get("count")) or 0.0
            p = fnum(f.get("yes_price_dollars"))
            sz += c
            if p is not None:
                pxw += c * p
            if f.get("is_taker"):
                tak += c
        return sz, ((pxw / sz) if sz > 0 and pxw > 0 else None), tak

    def _finalize_fill(self, tkr, series, side, ask, rem, z, oid, att=None,
                       terminal=False):
        """Accounting for a GTC order. Poll path (terminal=False):
        finalize ONLY on a full fill — a partial must keep its resting
        registration so the z-watchdog stays on it and later fills
        accumulate into one true row (XRP-1215 2026-08-10: finalizing
        the first 1.0 of 2.0 dropped the second fill from the ledger).
        Terminal path: book whatever actually filled. Returns True if
        a row was written."""
        sz, ypx, tak = self._fills_for_oid(tkr, oid)
        if not terminal:
            total = min(sz, CONTRACTS)
            if total < CONTRACTS - 1e-9:
                return False
        else:
            fb = 0.0
            rst0 = self.resting.get(tkr) or {}
            if rst0.get("oid2"):
                fb, _, _ = self._fills_for_oid(tkr, rst0["oid2"])
            pos = abs(self.position_of(tkr) or 0.0)
            # position minus the fallback leg's fills — never book the
            # other leg's contracts into this row
            total = min(max(sz, pos - fb), CONTRACTS)
            if total < 1e-9:
                return False
        rst = self.resting.get(tkr) or {}
        if ypx is not None:
            entry = ypx if side == "yes" else 1.0 - ypx
        else:
            entry = rst.get("limit", ask)
        mk = max(0.0, min(total - min(tak, total), total))
        self.entered.add(tkr)
        _rst = self.resting.pop(tkr, None) or {}
        self._save_resting()
        if _rst.get("oid2"):
            try:
                self.client.cancel_order_v2(_rst["oid2"])
            except KalshiAPIError:
                pass
            # AUDIT ROUND-2 (2026-08-10): both legs can fill in the same
            # window — a cancel is not proof of emptiness. Probe the
            # fallback's fills and book them on their own maker96 row
            # (idempotent via _fb_done). Residual risk: a staged fills-
            # index write inside this probe can still hide a just-landed
            # fill; the startup position sweep is the backstop.
            self._finalize_fallback(tkr, _rst)
        self.log_trade({
            "ts": int(time.time()), "ticker": tkr, "series": series,
            "side": side, "entry": round(entry, 4), "count": round(total, 4),
            "rem": round(rem, 2), "z": (round(z, 2) if z is not None else None),
            "signal_ask": ask, "result": "open", "lane": "cushion",
            "maker_ct": round(mk, 4), "taker_ct": round(total - mk, 4),
            "leg": _rst.get("leg", "capped"),
            "k_src": self.k_src.get(tkr, "official"),
        })
        if att is not None:
            att["outcome"] = "filled"
            att["fill"] = round(entry, 4)
            att["count"] = round(total, 4)
            self._log_attempt(att)
        LOG.info("FILLED %s %.2f @ %.3f (%s, sig %.2f, z=%s)",
                 tkr, total, entry,
                 "maker" if mk > 1e-9 else "taker", ask,
                 f"{z:.2f}" if z is not None else "?")
        return True

    def _rest_postmortem(self, tkr, rst, now):
        """Did ANY trade print at <= our resting price while we rested?
        Yes and no fill = implementation defect BY DEFINITION (a resting
        order sits in the queue; trades through its price must hit it).
        No = the price cap refused to overpay — the rule, not a bug."""
        if now - rst.get("placed", 0) < 120:
            # minute-candles cannot adjudicate a sub-2-minute rest: the
            # candle spanning the rest includes trades from before/after
            # it. First live IMPL_SUSPECT (2026-08-10 09:2x) was exactly
            # this false positive — 279 in-rest trades, none at our
            # price, flagged by pre-placement candle lows.
            return "rest_too_short_to_audit", None
        try:
            d = self.client._request(
                "GET", "/markets/candlesticks",
                params={"market_tickers": tkr,
                        "start_ts": int(rst["placed"]) + 60,
                        "end_ts": int(now) - 30, "period_interval": 1})
            cs = ((d or {}).get("markets") or [{}])[0].get("candlesticks") or []
            lo = hi = None
            for c in cs:
                pr = c.get("price") or {}
                l = fnum(pr.get("low_dollars"))
                h = fnum(pr.get("high_dollars"))
                if l is not None:
                    lo = l if lo is None else min(lo, l)
                if h is not None:
                    hi = h if hi is None else max(hi, h)
            if lo is None and hi is None:
                return "cap_refusal_no_trades", None
            if rst["side"] == "yes":
                tradable = lo is not None and lo <= rst["limit"] + 1e-9
                ext = lo
            else:
                nolimit = 1.0 - rst["limit"]
                tradable = hi is not None and hi >= nolimit - 1e-9
                ext = (1.0 - hi) if hi is not None else None
            return ("impl_suspect" if tradable else "cap_refusal"), ext
        except Exception:                               # noqa: BLE001
            return "postmortem_failed", None

    def _finalize_fallback(self, tkr, rst):
        """Terminal accounting for the 1ct post_only fallback leg. Its own
        ledger row, leg=maker96 — the pilot judges itself on this line."""
        oid2 = rst.get("oid2")
        if not oid2 or oid2 in self._fb_done:
            return False
        sz, ypx, tak = self._fills_for_oid(tkr, oid2)
        if sz < 1e-9:
            return False
        self._fb_done.add(oid2)
        side = rst["side"]
        entry = (ypx if side == "yes" else 1.0 - ypx) if ypx else REST_MAX
        total = min(sz, REST_MAX_CT)
        self.entered.add(tkr)
        self.log_trade({
            "ts": int(time.time()), "ticker": tkr, "series": rst["series"],
            "side": side, "entry": round(entry, 4), "count": round(total, 4),
            "rem": 0, "z": rst.get("z0"), "signal_ask": rst.get("sig"),
            "result": "open", "lane": "cushion", "leg": "maker96",
            "maker_ct": round(total - min(tak, total), 4),
            "taker_ct": round(min(tak, total), 4),
        })
        LOG.info("FILLED-FALLBACK %s %.2f @ %.3f [maker96 pilot]",
                 tkr, total, entry)
        return True

    def _order_snapshot(self, oid):
        """Latest order state, websocket first and REST as authority fallback."""
        streamed = self.kws.order_state(oid)
        if streamed is not None:
            return streamed[1]
        try:
            data = self.client._request("GET", f"/portfolio/orders/{oid}")
        except KalshiAPIError:
            return None
        return (data or {}).get("order") or data

    def _queue_position(self, oid):
        try:
            data = self.client._request(
                "GET", f"/portfolio/orders/{oid}/queue_position"
            )
        except KalshiAPIError:
            return None
        return fnum((data or {}).get("queue_position_fp")
                    or (data or {}).get("queue_position"))

    def _maybe_place_fallback(self, tkr, rst, now):
        """Place maker96 only after the primary is confirmed still resting."""
        if rst.get("fallback_attempted") or now < rst.get("fallback_due", 0):
            return False
        primary_size, _, _ = self._fills_for_oid(tkr, rst.get("oid"))
        if primary_size > 1e-9 or abs(self.position_of(tkr) or 0.0) > 1e-9:
            rst["fallback_attempted"] = True
            self._save_resting()
            return False
        order = self._order_snapshot(rst.get("oid"))
        if not order:
            return False
        filled = (fnum(order.get("fill_count_fp"))
                  or fnum(order.get("fill_count")) or 0.0)
        remaining = (fnum(order.get("remaining_count_fp"))
                     or fnum(order.get("remaining_count")) or 0.0)
        status = str(order.get("status") or "").lower()
        if filled > 1e-9 or remaining <= 1e-9 or status not in ("resting", "open"):
            rst["fallback_attempted"] = True
            self._save_resting()
            return False
        side = rst["side"]
        oside = "bid" if side == "yes" else "ask"
        price = round(REST_MAX if side == "yes" else 1.0 - REST_MAX, 4)
        oid2 = None
        try:
            response = self.client.create_order_v2(
                ticker=tkr, side=oside, count=REST_MAX_CT, price=price,
                client_order_id=str(uuid.uuid4()),
                time_in_force="good_till_canceled",
                expiration_time=int(rst["exp"]), post_only=True,
            )
            oid2 = ((response.get("order") or {}).get("order_id")
                    or response.get("order_id")) \
                if isinstance(response, dict) else None
        except KalshiAPIError:
            oid2 = None
        rst["fallback_attempted"] = True
        rst["oid2"] = oid2
        self._save_resting()
        self._log_attempt({
            "ts": now, "ticker": tkr, "side": side,
            "signal_ask": rst.get("sig"), "z": rst.get("z0"),
            "outcome": "fallback_submitted" if oid2 else "fallback_refused",
            "order_id": oid2,
        })
        if oid2:
            LOG.info("FALLBACK %s 1ct @ %.2f post-only submitted", tkr,
                     REST_MAX)
        return bool(oid2)

    def manage_resting(self, now):
        """Watchdog for standing GTC orders: cancel on z-death, finalize on
        fill or expiry. At most ONE remote action per tick — the scan loop
        must never stall behind order management."""
        for tkr in list(self.resting):
            rst = self.resting[tkr]
            meta = self.meta.get(tkr)
            series = meta[0] if meta else rst["series"]
            K = meta[1] if meta else None
            cts = meta[2] if meta else None
            rem = ((cts - now) / 60.0) if cts else 0.0
            spot, sigma, _ = self.spot_state(series)
            zc = cushion_z(spot, K, rst["side"], sigma, rem) if K else None
            expired = now >= rst["exp"] - 1 or rem <= REM_LO
            dead = (zc is None or zc < Z_MIN)
            if dead or expired:
                if not expired:
                    try:
                        self.client.cancel_order_v2(rst["oid"])
                    except KalshiAPIError:
                        pass
                if rst.get("oid2"):
                    try:
                        self.client.cancel_order_v2(rst["oid2"])
                    except KalshiAPIError:
                        pass
                    self._finalize_fallback(tkr, rst)
                if not self._finalize_fill(tkr, series, rst["side"],
                                           rst["sig"], rem, zc, rst["oid"],
                                           terminal=True):
                    self.resting.pop(tkr, None)
                    # registry persisted after the pop (audit item 4);
                    # the finalize-success path saves inside its own pop
                    self._save_resting()
                    pm, ext = self._rest_postmortem(tkr, rst, now)
                    if pm == "impl_suspect":
                        LOG.warning("IMPL_SUSPECT %s: trades printed at/thru "
                                    "our resting price %.3f (best %.3f) and "
                                    "the order did NOT fill — implementation "
                                    "defect by definition", tkr,
                                    rst["limit"], ext or -1)
                    self._log_attempt({
                        "ts": int(now), "ticker": tkr, "side": rst["side"],
                        "signal_ask": rst["sig"], "rem": round(rem, 2),
                        "z": (round(zc, 2) if zc is not None else None),
                        "outcome": ("rest_expired" if expired
                                    else "rest_canceled_z"),
                        "postmortem": pm,
                        "best_px": (round(ext, 3) if ext is not None
                                    else None)})
                    LOG.info("REST %s %s (z=%s rem=%.1f)",
                             "expired" if expired else "canceled-z", tkr,
                             f"{zc:.2f}" if zc is not None else "?", rem)
                return
            # WS fill event => probe NOW instead of waiting out the 2s
            # pacing (audit item 2; accelerator only — /portfolio/fills
            # stays the accounting authority). XRP-1215's second fill
            # landed 4s after the first; this closes that gap to ~0.
            ws_hit = (self.kws.fill_event_for(rst.get("oid"),
                                              rst.get("last_poll", 0))
                      or self.kws.fill_event_for(rst.get("oid2"),
                                                 rst.get("last_poll", 0)))
            order_hit = (self.kws.order_state(rst.get("oid"),
                                              rst.get("last_poll", 0))
                         or self.kws.order_state(rst.get("oid2"),
                                                 rst.get("last_poll", 0)))
            if ws_hit or order_hit or now - rst.get("last_poll", 0) >= 2.0:
                rst["last_poll"] = now
                if self._finalize_fill(tkr, series, rst["side"], rst["sig"],
                                       rem, zc, rst["oid"], rst.get("att")):
                    return
                if rst.get("oid2") and self._finalize_fallback(tkr, rst):
                    rst["oid2"] = None
                    self._save_resting()
                return
            if self._maybe_place_fallback(tkr, rst, now):
                return
            if (not rst.get("queue_checked")
                    and now - rst.get("placed", now) >= 1.0):
                queue = self._queue_position(rst.get("oid"))
                if queue is not None:
                    rst["queue_checked"] = True
                    rst["queue_ahead"] = queue
                    self._save_resting()
                    self._log_attempt({
                        "ts": now, "ticker": tkr, "side": rst["side"],
                        "signal_ask": rst.get("sig"), "z": rst.get("z0"),
                        "outcome": "queue_snapshot",
                        "queue_ahead_fp": queue,
                    })
                    return

    def _log_attempt(self, att):
        try:
            with open(ATTEMPTS_LOG, "a") as f:
                f.write(json.dumps(att) + "\n")
        except Exception:                                   # noqa: BLE001
            pass

    def reconcile(self):
        live = self.positions()
        rows = self.trades()
        changed = False
        for r in rows:
            if r.get("result") != "open":
                continue
            if abs(live.get(r["ticker"], 0.0)) > 1e-9:
                continue
            try:
                d = self.client._request("GET", f"/markets/{r['ticker']}")
                m = (d or {}).get("market", {})
            except KalshiAPIError:
                continue
            if m.get("result") not in ("yes", "no"):
                continue
            won = (m["result"] == r["side"])
            px = float(r["entry"])
            tak = r.get("taker_ct")
            tak = r["count"] if tak is None else float(tak)
            # STOP-AWARE ACCOUNTING: contracts sold early realise their
            # exit price; only the remainder rides to settlement. Without
            # this the settlement would be booked on contracts we no
            # longer held.
            ex_ct = float(r.get("exit_ct") or 0.0)
            ex_px = float(r.get("exit_px") or 0.0)
            rest = max(0.0, r["count"] - ex_ct)
            base = (1 - px) if won else -px
            pnl = base * rest - fee(px) * tak
            if ex_ct > 1e-9:
                pnl += (ex_px - px) * ex_ct - fee(ex_px) * ex_ct
                r["would_have"] = "win" if won else "loss"
            r["result"] = ("stopped" if ex_ct >= r["count"] - 1e-9
                           else ("win" if won else "loss"))
            r["pnl"] = round(pnl, 4)
            r["settled_day"] = et_day()
            changed = True
            LOG.info("SETTLED %s %s pnl=%+.4f", r["ticker"], r["result"], pnl)
        if changed:
            with open(LEDGER, "w") as f:
                for r in rows:
                    f.write(json.dumps(r) + "\n")

    def strike_proxy(self, series, open_ts):
        """The rules-defined strike (60s pre-open BRTI average),
        approximated by the close of our pre-open 1-min spot bar."""
        product = SERIES.get(series)
        if not product:
            return None
        bars = self.spot.bars.get(product) or {}
        for back in (60, 120, 180):
            b = bars.get(int(open_ts) - back)
            if b:
                return b
        return None

    def discover(self):
        """REST discovery of open markets: strikes, closes, WS subscriptions.
        Runs every DISCOVER_S; quotes flow over the websocket in between."""
        now = time.time()
        ok = 0
        for series in SERIES:
            try:
                d = self.client.get_markets(series_ticker=series,
                                            status="open", limit=10)
                ok += 1
            except KalshiAPIError as e:
                # SILENT FAILURE COST 5 HOURS on 2026-08-08: discovery erred
                # quietly, meta decayed empty, the scanner ran over nothing.
                # Failures are now loud and counted.
                self.discover_fails = getattr(self, "discover_fails", 0) + 1
                LOG.warning("discover failed %s (#%d): %s",
                            series, self.discover_fails, e)
                continue
            for m in (d or {}).get("markets", []):
                tkr = m.get("ticker")
                K = fnum(m.get("floor_strike"))
                cts = ts_of(m.get("close_time"))
                if not (tkr and cts):
                    continue
                if K:
                    old = self.meta.get(tkr)
                    if old and old[1] and self.k_src.get(tkr) == "computed":
                        delta = (K - old[1]) / K * 10000
                        LOG.info("strike official for %s: %.2f (proxy was "
                                 "%.2f, delta %.1fbps)", tkr, K, old[1], delta)
                    self.meta[tkr] = (series, K, cts)
                    self.k_src[tkr] = "official"
                else:
                    # KALSHI RESTRUCTURE 2026-08-10: strike publishes LATE
                    # ('Target price: TBD'). The rules define it as the 60s
                    # BRTI average before window open — proxy it from our
                    # spot feed (pre-open 1-min close, bps-accurate) so the
                    # lane is not blind; upgraded when official appears.
                    if self.k_src.get(tkr) != "computed":
                        prox = self.strike_proxy(series, cts - 15 * 60)
                        if prox:
                            self.meta[tkr] = (series, prox, cts)
                            self.k_src[tkr] = "computed"
                            LOG.info("strike TBD for %s — using spot proxy "
                                     "%.2f", tkr, prox)
                    elif tkr not in self.meta:
                        continue   # no strike yet and no proxy — skip
                # SEED FOR BOTH strike sources (bug 2026-08-10 11:4x: seeding
                # was nested in the TBD branch, so official-strike markets
                # never seeded and the scanner dropped them -> 0 rows)
                if tkr in self.meta:
                    b = fnum(m.get("yes_bid_dollars"))
                    a = fnum(m.get("yes_ask_dollars"))
                    if b is not None and a is not None and 0 <= b <= a <= 1:
                        cached = self.kws.quotes.get(tkr)
                        if (cached is None or not self.kws.connected
                                or now - cached[2] > DISCOVER_S + 5):
                            self.kws.quotes[tkr] = (b, a, now)
                            self.kws.seeded[tkr] = now
        self.meta = {t: v for t, v in self.meta.items() if v[2] > now - 60}
        LOG.info("discover: ok=%d meta=%d %s", ok, len(self.meta),
                 {s2[2:5]: sum(1 for t, v in self.meta.items() if v[0] == s2)
                  for s2 in SERIES})
        if ok:
            self.discover_fails = 0
        self.kws.ensure(list(self.meta))
        self.kws.prune(list(self.meta))
        orderbook_ws = getattr(self, "ows", None)
        if orderbook_ws:
            orderbook_ws.ensure(list(self.meta))
            orderbook_ws.prune(list(self.meta))
        self.last_discover = now
        # watchdog: an empty scanner while discovery "works" is a wedge, not
        # a quiet market — crypto 15M markets exist around the clock
        if not self.meta:
            self.empty_since = getattr(self, "empty_since", now)
            if now - self.empty_since > 300:
                LOG.error("WATCHDOG: no markets discovered for %.0f min — "
                          "scanner is blind, investigate/restart",
                          (now - self.empty_since) / 60)
        else:
            self.empty_since = None if not hasattr(self, "empty_since") else now

    def spot_state(self, series):
        """(spot for the GATE, sigma, live spot for display).

        FIDELITY FIX 2026-08-08 21:30: the validated rule computes z from the
        last COMPLETED 1-min bar. Using the live streaming price changed which
        trades qualify (ETH 21:05: bar-z +1.67 qualified, live-z read lower,
        entry silently refused). The gate uses the validated input; the live
        price is display/diagnostic only."""
        product = SERIES[series]
        # Seal Coinbase websocket trades at the minute boundary. This removes
        # the old 0-30s REST refresh delay while preserving the validated
        # completed-bar input exactly.
        self.spot.ingest_completed(
            product, self.cws.completed_bars(product, max_bars=120)
        )
        bar_close, sigma = self.spot.state(product)
        live = self.cws.price(product)
        return bar_close, sigma, live

    def _refresh_orderbooks(self, now, force=False):
        """Refresh every watched executable book in one REST request.

        Kalshi's market summaries are not an execution source. This batch
        endpoint returns the actual resting YES and NO bids, from which both
        executable asks are derived. Websocket ticker events still wake the
        hot loop, but they cannot qualify a trade by themselves.
        """
        if not self.meta:
            return
        # A connected, sequence-checked websocket snapshot remains current
        # until a delta changes it. REST is the fail-safe for missing books,
        # reconnects and initial subscription warm-up, not the hot path.
        orderbook_ws = getattr(self, "ows", None)
        missing = [ticker for ticker in self.meta
                   if not orderbook_ws or orderbook_ws.quote(ticker) is None]
        if (not force and orderbook_ws
                and orderbook_ws.age() < WS_STALE and not missing):
            return
        if not force and now - self.last_book_refresh < BOOK_REFRESH_S:
            return
        self.last_book_refresh = now
        tickers = list(self.meta)
        try:
            data = self.client._request(
                "GET", "/markets/orderbooks",
                params=[("tickers", ticker) for ticker in tickers],
            )
        except KalshiAPIError as exc:
            if now - self.last_book_error_log >= 30:
                LOG.warning("executable book refresh failed: %s", exc)
                self.last_book_error_log = now
            return
        received = time.time()
        seen = set()
        for row in (data or {}).get("orderbooks", []) or []:
            ticker = row.get("ticker")
            if not ticker:
                continue
            book = row.get("orderbook_fp") or row.get("orderbook") or {}
            quote = executable_quote_from_book(book)
            if quote is None:
                self.book_quotes.pop(ticker, None)
                continue
            self.book_quotes[ticker] = (quote[0], quote[1], received)
            seen.add(ticker)
        # An omitted/empty market is not allowed to retain previously real
        # depth until BOOK_MAX_AGE; this snapshot is authoritative now.
        for ticker in tickers:
            if ticker not in seen:
                self.book_quotes.pop(ticker, None)
        active = set(tickers)
        for ticker in list(self.book_quotes):
            if ticker not in active:
                self.book_quotes.pop(ticker, None)

    def _book_quote(self, ticker, now=None):
        orderbook_ws = getattr(self, "ows", None)
        streamed = orderbook_ws.quote(ticker) if orderbook_ws else None
        if streamed is not None:
            return streamed[0], streamed[1], streamed[2]
        quote = self.book_quotes.get(ticker)
        now = time.time() if now is None else now
        if not quote or now - quote[2] > BOOK_MAX_AGE:
            return None
        return quote

    def poll(self):
        """Evaluate only executable order-book quotes, never summaries."""
        now = time.time()
        self._refresh_orderbooks(now)
        # The batch response is timestamped when it arrives. Re-read the clock
        # so quote ages and minutes remaining are never computed from a time
        # captured before the network round trip.
        now = time.time()
        open_pos = self.open_positions()
        active_tickers = ({p.get("ticker") for p in open_pos}
                          | set(self.resting))
        held_series = ({p.get("series") for p in open_pos}
                       | {row.get("series") for row in self.resting.values()})
        scan = []
        for tkr, (series, K, cts) in list(self.meta.items()):
            q = self._book_quote(tkr, now)
            if not q:
                continue
            b, a, qts = q
            if not (0 < b <= a < 1):
                continue
            rem = (cts - now) / 60.0
            if rem <= 0 or rem > 14:
                continue
            spot, sigma, live_spot = self.spot_state(series)
            mid = (b + a) / 2
            side = "yes" if mid > 0.5 else "no"
            fb, fa = (b, a) if side == "yes" else (1 - a, 1 - b)
            z = cushion_z(spot, K, side, sigma, rem)
            z_live = cushion_z(live_spot, K, side, sigma, rem)
            why = gates(fb, fa, rem, z)
            scan.append({"series": series, "tkr": tkr, "side": side,
                         "fa": fa, "rem": round(rem, 1),
                         "spot": spot, "K": K,
                         "z": (round(z, 2) if z is not None else None),
                         "z_live": (round(z_live, 2) if z_live is not None
                                    else None),
                         "quote_source": "executable_orderbook",
                         "quote_age_s": round(now - qts, 3),
                         "why": why or "ENTER"})
            if z is not None and z >= 1.2 and 0.85 <= fa < 0.93:
                # near-signal trace: makes estimator divergence diagnosable
                try:
                    with open(os.path.join(STATE, "near_signals.jsonl"), "a") as nf:
                        nf.write(json.dumps({
                            "ts": int(now), "tkr": tkr, "side": side,
                            "fa": fa, "rem": round(rem, 2),
                            "z_bar": round(z, 3),
                            "z_live": (round(z_live, 3) if z_live else None),
                        }) + "\n")
                except Exception:                           # noqa: BLE001
                    pass
            if any(tkr.startswith(p) for p in PAUSED_SERIES):
                continue        # series paused for NEW entries (user)
            if why or self.halted or tkr in self.entered \
                    or tkr in self.resting:
                continue
            if self.attempts.get(tkr, 0) >= MAX_ATTEMPTS:
                continue
            if series in held_series:
                continue
            if len(active_tickers) >= MAX_CONCURRENT:
                continue
            self.take(tkr, series, side, fa, rem, z, quote_ts=qts)
            open_pos = self.open_positions()
            active_tickers = ({p.get("ticker") for p in open_pos}
                              | set(self.resting))
            held_series = ({p.get("series") for p in open_pos}
                           | {row.get("series")
                              for row in self.resting.values()})
        self.manage_resting(now)
        if now - self.last_scan_write > 2.0:
            self.last_scan_write = now
            try:
                with open(SCAN_FILE, "w") as f:
                    json.dump({"ts": int(now), "rows": scan}, f)
            except Exception:                               # noqa: BLE001
                pass

    def run(self, minutes):
        end = time.time() + minutes * 60
        _iters = 0
        _iter_t0 = time.time()
        LOG.info("cushion bot up — %d assets, %.0f ct, max %d, live=%s, "
                 "WS hot loop %.1fs / REST fallback %ds",
                 len(SERIES), CONTRACTS, MAX_CONCURRENT, self.live,
                 TICK_S, POLL_S)
        if PAUSED_SERIES:
            LOG.warning("PAUSED (no new entries): %s",
                        ",".join(PAUSED_SERIES))
        LOG.info("stop: %s trigger %.2f (hold %.0fs) floor %.2f "
                 "cross %.2f excl %s",
                 "ON" if STOP_ON else "OFF", STOP_TRIGGER, STOP_CONFIRM_S,
                 STOP_FLOOR, STOP_CROSS, ",".join(STOP_EXCLUDE))
        LOG.info("rule: fav ask %.2f-%.2f, rem %d-%dm, z>=%.1f, spread<=%.2f, "
                 "40c reduce-only disaster exit on BTC/ETH/SOL",
                 BAND_LO, BAND_HI, REM_LO, REM_HI, Z_MIN, MAX_SPREAD)
        last_bars = last_house = 0.0
        ws_was_live = True
        while time.time() < end:
            try:
                now = time.time()
                ws_live = self.kws.age() < WS_STALE
                if ws_live != ws_was_live:
                    LOG.warning("kalshi ws %s — %s",
                                "LIVE" if ws_live else "STALE",
                                "hot loop" if ws_live else "REST fallback")
                    ws_was_live = ws_live
                self.manage_stops(now)
                if now - self.last_discover > (DISCOVER_S if ws_live else POLL_S):
                    self.discover()          # also reseeds quotes for fallback
                if now - last_bars > 30:
                    last_bars = now
                    self.spot.refresh()      # 1-min bars for sigma
                self.check_halt()
                if not self.halted:
                    self.poll()
                if now - last_house > 60:
                    last_house = now
                    self.reconcile()
                    _el = max(1e-6, time.time() - _iter_t0)
                    _period = _el / max(1, _iters) * 1000
                    _fired, _false, _done = self.stop_stats()
                    LOG.info("open=%d realised_today=%+.4f halted=%r "
                             "ws_age=%.1fs cb_age=%.1fs loop=%.0fms "
                             "stops=%d(false %d/%d)",
                             len(self.open_positions()), self.realised_today(),
                             self.halted, self.kws.age(),
                             time.time() - self.cws.last_msg
                             if self.cws.last_msg else -1,
                             _period, _fired, _false, _done)
                    _iters = 0
                    _iter_t0 = time.time()
            except Exception as e:                          # noqa: BLE001
                LOG.exception("loop error: %s", e)
            # wake INSTANTLY on any websocket tick instead of a fixed
            # timer (measured 2026-08-09: qualifying states lived a median
            # 1.8s before our first order — mostly our own polling cadence
            # stacked on serial confirm blocks)
            _iters += 1
            if self.kws.age() < WS_STALE:
                self.kws.wake.wait(timeout=TICK_S)
                self.kws.wake.clear()
            else:
                time.sleep(POLL_S)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true", help="place REAL orders")
    ap.add_argument("--minutes", type=float, default=60 * 24 * 365)
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    CushionBot(live=args.live).run(args.minutes)
