#!/usr/bin/env python3
"""Sports lock-lane LIVE bot — 97-99c held favorites, tennis + WNBA.

Validated 2026-08-03: 5,156-game backtest (all gates) + 93W/0L forward
paper. Entry: favorite mid in [0.97,0.99), held >=10 consecutive minute
closes, spread <=2c, IOC at ask <=0.99, ONE position per match (event).
Risk: 10ct/match, max 4 concurrent, daily loss halt -$15, HALT file,
two-key arming (--live AND live:true in sports_config.yaml).
"""
import json, logging, os, sys, time, uuid, argparse
from datetime import datetime
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))
BOT = "/Users/varmakammili/Documents/GitHub/StocksTradingTest/kalshi_unbroken_bot"
sys.path.insert(0, BOT)
from kalshi_client import Credentials, KalshiAPIError, KalshiClientV2  # noqa: E402
import yaml  # noqa: E402

ET = ZoneInfo("America/New_York")
STATE = os.path.join(HERE, "state")
LOG = logging.getLogger("sportsbot")
SERIES = ["KXWTAMATCH", "KXWNBAGAME"]
# LIVE = validated families ONLY (08-03 pm audit): WTA H10 101-0 +1.20c,
# WNBA H5 57-0 +1.14c (user re-added 08-03 pm; slot-crowding moot at
# these signal rates). ITF/ITFW/Challengers pulled from live pending
# their own backtests (partial ITF: 140-1, OOS -1.10c on deployed rule).
# ATP main REMOVED 08-03 pm: event-deduped replay NEGATIVE in every 97-99
# cell (H5 -1.83c/ct, OOS -4.61; H10 -0.57, OOS -4.09).
BAND_LO, BAND_HI, HOLD, MAX_SPREAD, MAX_ENTRY = 0.97, 0.99, 10, 0.02, 0.99
HOLD_BY = {"KXWNBAGAME": 5}       # per-family validated hold; else HOLD


def et_day(ts=None):
    dt = datetime.fromtimestamp(ts, ET) if ts else datetime.now(ET)
    return dt.strftime("%Y-%m-%d")


def fee(p):
    return 0.07 * p * (1 - p)


def fav_of(b, a):
    m = (b + a) / 2
    return ("yes", b, a, m) if m > 0.5 else ("no", 1 - a, 1 - b, 1 - m)


class SportsBot:
    def __init__(self, cfg, live):
        self.cfg = cfg
        dry = not (live and cfg.get("live", False))
        self.client = KalshiClientV2(creds=Credentials.load(), dry_run=dry)
        self.dry = dry
        self.closes = {}       # ticker -> [(minute, side, mid, bid, ask)]
        self.last_quote = {}
        self.last_minute = {}
        self.event_of = {}     # ticker -> event_ticker
        self.entered_events = set()
        self.pending = {}      # ticker -> resting maker order state
        self.trades_path = os.path.join(STATE, "live_trades.jsonl")
        os.makedirs(STATE, exist_ok=True)
        for t in self.trades():
            if t.get("event"):
                self.entered_events.add(t["event"])
        self.last_settle = 0.0
        self.last_reconcile = 0.0
        self.halted = ""
        self.stop = False

    def trades(self):
        if not os.path.exists(self.trades_path):
            return []
        return [json.loads(l) for l in open(self.trades_path)]

    def log_trade(self, rec):
        with open(self.trades_path, "a") as f:
            f.write(json.dumps(rec) + "\n")

    def realized_today(self):
        d = et_day()
        return sum(t.get("pnl", 0) for t in self.trades()
                   if t.get("result") in ("yes_win", "loss", "win")
                   and t.get("settled_day") == d)

    def open_positions(self):
        return [t for t in self.trades() if t.get("result") == "open"]

    def check_halts(self):
        new = ""
        if os.path.exists(os.path.join(STATE, "HALT")):
            new = "HALT file"
        elif self.realized_today() <= -abs(self.cfg.get("daily_loss_halt_usd", 15)):
            new = "daily loss halt"
        if new != self.halted:
            LOG.warning("HALT -> %r", new or "cleared")
        self.halted = new

    def enter(self, tkr, series, side, bid, ask):
        ev = self.event_of.get(tkr, tkr)
        if ev in self.entered_events or self.halted:
            return
        if any(p["ev"] == ev for p in self.pending.values()):
            return
        if len(self.open_positions()) + len(self.pending) \
                >= self.cfg.get("max_concurrent", 4):
            return
        n = float(self.cfg.get("contracts", 10))
        cap = min(ask, MAX_ENTRY)
        if self.dry:
            LOG.info("DRY would enter %s %s %.0fct @<=%.3f", tkr, side, n, cap)
            return
        if self.cfg.get("maker_first", True):
            if self.place_maker(tkr, series, ev, side, round(cap - 0.01, 4), n):
                return                      # resting; managed in check_pending
        self.take(tkr, series, ev, side, cap, n)

    def place_maker(self, tkr, series, ev, side, px, n):
        """Rest a bid 1c under the ask (verified +0.7-0.95c/ct vs taker).
        Server-side expiration is the zombie-order safety net."""
        if px <= 0.0:
            return False
        oside, opx = (("bid", px) if side == "yes"
                      else ("ask", round(1 - px, 4)))
        try:
            resp = self.client.create_order_v2(
                ticker=tkr, side=oside, count=n, price=opx,
                client_order_id=str(uuid.uuid4()),
                time_in_force="good_till_canceled",
                expiration_time=int(time.time())
                + int(self.cfg.get("maker_wait_secs", 120)) + 15,
                post_only=True)
        except KalshiAPIError as e:
            LOG.warning("maker place failed %s: %s -> taker", tkr, e)
            return False
        oid = ((resp.get("order") or {}).get("order_id") or resp.get("order_id"))
        if not oid:
            return False
        self.pending[tkr] = {"oid": oid, "ev": ev, "series": series,
                             "side": side, "px": px, "n": n,
                             "placed": time.time()}
        LOG.info("MAKER resting %s %s %.0fct @ %.3f", tkr, side, n, px)
        return True

    def take(self, tkr, series, ev, side, cap, n):
        oside, px = (("bid", round(cap, 4)) if side == "yes"
                     else ("ask", round(1 - cap, 4)))
        try:
            resp = self.client.create_order_v2(
                ticker=tkr, side=oside, count=n, price=px,
                client_order_id=str(uuid.uuid4()),
                time_in_force="immediate_or_cancel", post_only=False)
        except KalshiAPIError as e:
            LOG.error("order failed %s: %s", tkr, e)
            return
        oid = ((resp.get("order") or {}).get("order_id") or resp.get("order_id"))
        got = cost = 0.0
        for wait in (1.5, 2.5):
            time.sleep(wait)
            got, cost = self.fills_for(tkr, oid, side, cap)
            if got > 1e-9:
                break
        if got > 1e-9:
            self.record_fill(tkr, series, ev, side, got, cost, maker=False)
        else:
            LOG.info("no fill %s (thin at cap) — will retry next close", tkr)

    def fills_for(self, tkr, oid, side, fallback_px):
        got = cost = 0.0
        try:
            d = self.client.get_fills(ticker=tkr, limit=20)
            for fl in d.get("fills", []):
                if oid and fl.get("order_id") == oid:
                    c = float(fl.get("count_fp") or 0)
                    fp = (fl.get("no_price_dollars") if side == "no"
                          else fl.get("yes_price_dollars"))
                    got += c
                    cost += c * float(fp or fallback_px)
        except KalshiAPIError:
            pass
        return got, cost

    def record_fill(self, tkr, series, ev, side, got, cost, maker):
        self.entered_events.add(ev)      # mark only on FILL — no-fills retry
        vwap = round(cost / got, 4)
        self.log_trade({"ts": int(time.time()), "ticker": tkr,
                        "series": series, "event": ev, "side": side,
                        "entry": vwap, "count": got, "result": "open",
                        "lane": "sports", "maker": maker})
        LOG.info("POSITION %s %s %s %.0fct @ %.3f",
                 "maker" if maker else "taker", tkr, side, got, vwap)

    def check_pending(self):
        """Manage resting maker bids: fill -> position; favorite weakens or
        flips -> cancel (signal died, no fallback); timeout -> cancel, then
        taker fallback if the entry conditions still hold."""
        for tkr in list(self.pending):
            p = self.pending[tkr]
            got, cost = self.fills_for(tkr, p["oid"], p["side"], p["px"])
            if got > 1e-9:
                if got < p["n"] - 1e-9:
                    try:
                        self.client.cancel_order_v2(p["oid"])
                    except KalshiAPIError:
                        pass
                self.record_fill(tkr, p["series"], p["ev"], p["side"],
                                 got, cost, maker=True)
                del self.pending[tkr]
                continue
            q = self.last_quote.get(tkr)
            fav = fav_of(*q) if q else None
            if fav and (fav[0] != p["side"] or fav[3] < BAND_LO):
                try:
                    self.client.cancel_order_v2(p["oid"])
                except KalshiAPIError:
                    pass
                got, cost = self.fills_for(tkr, p["oid"], p["side"], p["px"])
                if got > 1e-9:            # filled in the cancel race
                    self.record_fill(tkr, p["series"], p["ev"], p["side"],
                                     got, cost, maker=True)
                else:
                    LOG.info("MAKER canceled %s (weakened/flip %.3f)",
                             tkr, fav[3])
                del self.pending[tkr]
                continue
            if time.time() - p["placed"] >= self.cfg.get("maker_wait_secs", 120):
                try:
                    self.client.cancel_order_v2(p["oid"])
                except KalshiAPIError:
                    pass
                got, cost = self.fills_for(tkr, p["oid"], p["side"], p["px"])
                del self.pending[tkr]
                if got > 1e-9:            # filled in the cancel race
                    self.record_fill(tkr, p["series"], p["ev"], p["side"],
                                     got, cost, maker=True)
                elif fav and fav[0] == p["side"] and fav[3] >= BAND_LO \
                        and fav[2] - fav[1] <= MAX_SPREAD and fav[2] <= MAX_ENTRY:
                    LOG.info("MAKER timeout %s -> taker fallback", tkr)
                    self.take(tkr, p["series"], p["ev"], p["side"],
                              min(fav[2], MAX_ENTRY), p["n"])
                else:
                    LOG.info("MAKER timeout %s, conditions gone", tkr)

    def poll_markets(self):
        now = time.time()
        scan = {}
        approaching = {}
        for series in SERIES:
            try:
                d = self.client.get_markets(series_ticker=series,
                                            status="open", limit=200)
            except KalshiAPIError:
                continue
            for m in d.get("markets", []):
                tkr = m["ticker"]
                try:
                    b = float(m.get("yes_bid_dollars") or 0)
                    a = float(m.get("yes_ask_dollars") or 1)
                except (TypeError, ValueError):
                    continue
                if not (0 < b <= a < 1):
                    continue
                self.event_of[tkr] = m.get("event_ticker", tkr)
                mb = int(now // 60)
                if tkr in self.last_minute and mb > self.last_minute[tkr] \
                        and tkr in self.last_quote:
                    pb, pa = self.last_quote[tkr]
                    s_, fb, fa, fm = fav_of(pb, pa)
                    seq = self.closes.setdefault(tkr, [])
                    seq.append((mb * 60, s_, fm, fb, fa))
                    del seq[:-20]
                    i = len(seq) - 1
                    _, cs, cm, cb, ca = seq[i]
                    hold = HOLD_BY.get(series, HOLD)
                    if (BAND_LO <= cm < BAND_HI and ca - cb <= MAX_SPREAD
                            and ca <= BAND_HI + 0.005 and len(seq) >= hold
                            and all(seq[j][1] == cs and seq[j][2] >= BAND_LO
                                    for j in range(i - hold + 1, i))):
                        self.enter(tkr, series, cs, cb, ca)
                self.last_minute[tkr] = mb
                self.last_quote[tkr] = (b, a)
                scan.setdefault(series, set()).add(m.get("event_ticker", tkr))
                fm = max((b + a) / 2, 1 - (b + a) / 2)
                if 0.90 <= fm < 0.97:
                    ev = m.get("event_ticker", tkr)
                    if ev not in self.entered_events:
                        approaching[ev] = max(approaching.get(ev, 0), fm)
            time.sleep(0.25)
        try:
            with open(os.path.join(STATE, "scanner_status.json"), "w") as f:
                near = sorted(approaching.items(), key=lambda kv: -kv[1])
                json.dump({"ts": int(now),
                           "by": {k: len(v) for k, v in scan.items()},
                           "total": sum(len(v) for v in scan.values()),
                           "approaching": len(near),
                           "nearest": [[e[-16:], round(f2, 3)]
                                       for e, f2 in near[:12]]}, f)
        except Exception:
            pass

    def poll_settlements(self):
        if time.time() - self.last_settle < 60:
            return
        self.last_settle = time.time()
        rows = self.trades()
        dirty = False
        for t in rows:
            if t.get("result") != "open":
                continue
            try:
                m = self.client.get_market(t["ticker"]).get("market", {})
            except KalshiAPIError:
                continue
            r = m.get("result")
            if r in ("yes", "no"):
                won = (r == t["side"])
                fe = fee(t["entry"]) * t["count"]
                t["pnl"] = ((1 - t["entry"]) * t["count"] - fe) if won \
                    else (-t["entry"] * t["count"] - fe)
                t["result"] = "win" if won else "loss"
                t["settled_day"] = et_day()
                LOG.info("SETTLED %s %s -> %s pnl=$%.2f", t["ticker"],
                         t["side"], t["result"], t["pnl"])
                dirty = True
        if dirty:
            with open(self.trades_path, "w") as f:
                for t in rows:
                    f.write(json.dumps(t) + "\n")

    def reconcile(self):
        """Exchange truth: adopt untracked sports positions (fills lag)."""
        if time.time() - self.last_reconcile < 45:
            return
        self.last_reconcile = time.time()
        try:
            d = self.client.get_positions(limit=200)
        except KalshiAPIError:
            return
        known = {t["ticker"] for t in self.trades()}
        for p in d.get("market_positions", []) or []:
            tkr = p.get("ticker", "")
            if not any(tkr.startswith(s) for s in SERIES) or tkr in known:
                continue
            pos = float(p.get("position_fp") or p.get("position") or 0)
            if abs(pos) < 0.01:
                continue
            side = "yes" if pos > 0 else "no"
            LOG.warning("ADOPTED %s %s %.0fct (phantom fill)", tkr, side, abs(pos))
            self.log_trade({"ts": int(time.time()), "ticker": tkr,
                            "series": tkr.split("-")[0],
                            "event": self.event_of.get(tkr, tkr),
                            "side": side, "entry": 0.98, "count": abs(pos),
                            "result": "open", "lane": "sports",
                            "adopted": True})

    def run(self, minutes):
        LOG.info("sports bot start dry=%s contracts=%s", self.dry,
                 self.cfg.get("contracts"))
        deadline = time.time() + minutes * 60
        import signal as sg
        sg.signal(sg.SIGTERM, lambda *_: setattr(self, "stop", True))
        sg.signal(sg.SIGINT, lambda *_: setattr(self, "stop", True))
        while not self.stop and time.time() < deadline:
            try:
                self.check_halts()
                self.poll_markets()
                if not self.dry:
                    self.check_pending()
                    self.poll_settlements()
                    self.reconcile()
            except Exception as e:          # noqa: BLE001
                LOG.error("cycle error: %r", e)
            time.sleep(3)
        LOG.info("stopping")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--minutes", type=float, default=60 * 24 * 4)
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    fh = logging.FileHandler(os.path.join(HERE, "state", "bot.log"))
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logging.getLogger().addHandler(fh)
    cfg = yaml.safe_load(open(os.path.join(HERE, "sports_config.yaml")))
    SportsBot(cfg, args.live).run(args.minutes)
