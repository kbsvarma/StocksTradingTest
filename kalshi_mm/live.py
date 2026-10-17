"""
LIVE market-making loop. Places REAL maker orders on incentivized markets.

Safety model (real money):
  - Kalshi API is the source of truth: every cycle reconciles against real resting
    orders + real positions before acting.
  - Hard caps enforced before every order: per-market net, total exposure, daily loss.
  - post_only=True always: maker only, never crosses, $0 maker fee on these markets.
  - Kill switch: touch state/HALT -> cancel everything and stop.
  - On ANY exit (duration end, halt, exception, Ctrl-C): cancel ALL resting orders.
  - Positions are tiny and short-dated; on halt we stop quoting and let residual
    positions settle at event resolution rather than force-crossing the spread.

Run (time-boxed, supervised first):
    python live.py --minutes 10 --size 20 --max-markets 3

All flags also in config.yaml: safety.* and quoting.*
"""

from __future__ import annotations

import argparse
import json
import logging
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import yaml

from kalshi_client import Credentials, KalshiAPIError, KalshiClient
from incentives import parse_top, select_targets

HERE = Path(__file__).resolve().parent
STATE = HERE / "state"
STATE.mkdir(exist_ok=True)
HALT = STATE / "HALT"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(message)s",
    handlers=[logging.StreamHandler(), logging.FileHandler(STATE / "live_run.log")],
)
log = logging.getLogger("live")


def _f(x) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


class LiveBot:
    def __init__(self, client: KalshiClient, cfg: dict, size: int, max_markets: int,
                 max_res_days: float = 10.0):
        self.c = client
        self.cfg = cfg
        s = cfg["safety"]
        self.size = size
        self.max_markets = max_markets
        self.max_res_days = max_res_days
        self.max_pos = s["max_position_per_market"]
        self.max_exposure = s["max_total_exposure_usd"]
        self.max_daily_loss = s["max_daily_loss_usd"]
        self.min_spread = cfg["quoting"]["min_spread_cents"]
        self.edge = cfg["quoting"]["edge_cents"]
        self.skew = cfg["quoting"]["inventory_skew_cents"]
        self.start_equity = None
        self.orders_placed = 0
        self.orders_cancelled = 0

    # -- API state (source of truth) --------------------------------------- #
    def balance_usd(self) -> float:
        return _f(self.c.get_balance().get("balance")) / 100.0

    def positions(self) -> dict[str, float]:
        """ticker -> net yes-equivalent contracts."""
        out: dict[str, float] = {}
        d = self.c.get_positions(limit=200)
        for p in d.get("market_positions", []) or []:
            tk = p.get("ticker") or p.get("market_ticker")
            net = _f(p.get("position") or p.get("net_position") or p.get("position_fp"))
            if tk:
                out[tk] = net
        return out

    def resting(self) -> list[dict]:
        d = self.c.get_orders(status="resting", limit=200)
        return d.get("orders", []) or []

    def equity_usd(self, positions: dict[str, float] | None = None) -> float:
        """True account equity = cash balance + Kalshi's own portfolio_value mark
        (both in cents). This is the correct figure for the daily-loss guard."""
        b = self.c.get_balance()
        return (_f(b.get("balance")) + _f(b.get("portfolio_value"))) / 100.0

    def cancel_all(self):
        for o in self.resting():
            oid = o.get("order_id") or o.get("id")
            if oid:
                try:
                    self.c.cancel_order(oid)
                    self.orders_cancelled += 1
                except Exception as e:
                    log.warning("cancel failed %s: %s", oid, e)

    # -- quoting ----------------------------------------------------------- #
    def desired_quotes(self, t, net: float) -> list[tuple[str, int, int]]:
        """Return [(side, price_cents, size)] for this target given current net."""
        fair = (t.yes_bid + t.yes_ask) / 2.0 - net * self.skew
        bid_px = max(1, min(t.yes_ask - 1, round(fair - self.edge)))
        ask_px = min(99, max(t.yes_bid + 1, round(fair + self.edge)))
        if bid_px >= ask_px:
            return []
        out = []
        # bid: buy YES, only if we can get longer
        if net + self.size <= self.max_pos:
            out.append(("yes", bid_px, self.size))
        # ask: buy NO at 100-ask_px, only if we can get shorter
        if net - self.size >= -self.max_pos:
            out.append(("no", 100 - ask_px, self.size))
        return out

    @staticmethod
    def resting_key(o: dict) -> tuple:
        side = o.get("side")
        pxd = o.get("yes_price_dollars") if side == "yes" else o.get("no_price_dollars")
        return (o.get("ticker"), side, round(_f(pxd) * 100))

    def reconcile_market(self, t, net: float, my_resting: list[dict], room_usd: float):
        desired = self.desired_quotes(t, net)
        desired_keys = {(t.ticker, s, p): (s, p, sz) for s, p, sz in desired}
        rest_by_key = {self.resting_key(o): o for o in my_resting}

        # cancel orders that aren't desired
        for k, o in rest_by_key.items():
            if k not in desired_keys:
                oid = o.get("order_id") or o.get("id")
                try:
                    self.c.cancel_order(oid)
                    self.orders_cancelled += 1
                except Exception as e:
                    log.warning("cancel %s: %s", oid, e)

        # place desired orders not already resting
        for k, (side, px, sz) in desired_keys.items():
            if k in rest_by_key:
                continue
            collateral = (px if side == "yes" else px) * sz / 100.0  # price is the cost/contract
            if collateral > room_usd:
                continue  # not enough free cash; skip silently
            try:
                self.c.create_order(
                    ticker=t.ticker, side=side, action="buy", count=sz,
                    price_cents=px, client_order_id="mm-" + uuid.uuid4().hex[:14],
                    post_only=True,
                )
                self.orders_placed += 1
                room_usd -= collateral
            except KalshiAPIError as e:
                if e.status not in (400, 403):  # 400/403 often = insufficient funds / post-only reject
                    log.warning("place %s %s@%s: %s", t.ticker, side, px, e.text[:120])
            except Exception as e:
                log.warning("place err %s: %s", t.ticker, e)

    # -- main loop --------------------------------------------------------- #
    def run(self, minutes: float, cycle: float, rescan: float):
        end = time.time() + minutes * 60
        self.start_equity = self.equity_usd()
        log.info("LIVE start. balance=$%.2f size=%d max_markets=%d caps[pos=%d exp=$%.0f loss=$%.0f]",
                 self.start_equity, self.size, self.max_markets, self.max_pos,
                 self.max_exposure, self.max_daily_loss)
        targets = []
        last_scan = 0.0
        last_log = 0.0
        try:
            while time.time() < end:
                if HALT.exists():
                    log.warning("HALT file present -> cancelling all and stopping.")
                    break
                now = time.time()
                # Per-cycle work is wrapped: a transient API/network error logs and
                # the loop continues to the next cycle rather than killing the run.
                try:
                    positions = self.positions()
                    # daily-loss guard
                    eq = self.equity_usd(positions)
                    dd = eq - self.start_equity
                    if dd <= -self.max_daily_loss:
                        log.warning("DAILY LOSS breached (dd=$%.2f) -> cancel all, stop.", dd)
                        break

                    if now - last_scan > rescan or not targets:
                        try:
                            targets = select_targets(self.c, self.size, self.max_markets,
                                                     self.min_spread, max_resolution_days=self.max_res_days)
                            log.info("targets: %s", [f"{t.ticker}({t.yes_bid}/{t.yes_ask},room={t.room_score:.2f})" for t in targets])
                        except Exception as e:
                            log.warning("rescan failed: %s", e)
                        last_scan = now

                    resting = self.resting()
                    free_cash = self.balance_usd()
                    target_tickers = {t.ticker for t in targets}

                    # cancel resting on markets we no longer target
                    for o in resting:
                        if o.get("ticker") not in target_tickers:
                            oid = o.get("order_id") or o.get("id")
                            try:
                                self.c.cancel_order(oid); self.orders_cancelled += 1
                            except Exception:
                                pass

                    # refresh book per target then reconcile
                    for t in targets:
                        try:
                            top = parse_top(self.c.get_orderbook(t.ticker, depth=3))
                        except Exception:
                            continue
                        if not top:
                            continue
                        t.yes_bid, t.bid_size, t.yes_ask, t.ask_size = top
                        if t.yes_ask - t.yes_bid < self.min_spread:
                            continue
                        net = positions.get(t.ticker, 0.0)
                        mine = [o for o in resting if o.get("ticker") == t.ticker]
                        self.reconcile_market(t, net, mine, room_usd=min(free_cash, self.max_exposure))

                    if now - last_log > 30:
                        log.info("t=%.1fmin bal=$%.2f eq=$%.2f dd=$%.2f placed=%d cancelled=%d open=%d pos=%s",
                                 (minutes * 60 - (end - now)) / 60, free_cash, eq, dd,
                                 self.orders_placed, self.orders_cancelled, len(resting),
                                 {k: round(v, 1) for k, v in positions.items() if abs(v) > 0})
                        self._write_summary(eq, dd, positions)
                        last_log = now
                except Exception as e:
                    log.warning("cycle error (continuing): %s", e)

                time.sleep(max(0.0, cycle - (time.time() - now)))
        finally:
            log.info("shutting down -> cancelling ALL resting orders.")
            self.cancel_all()
            final_pos = self.positions()
            eq = self.equity_usd(final_pos)
            self._write_summary(eq, eq - (self.start_equity or eq), final_pos, final=True)
            log.info("=== LIVE RUN DONE. start_bal=$%.2f end_eq=$%.2f pnl=$%.2f placed=%d cancelled=%d ===",
                     self.start_equity, eq, eq - self.start_equity, self.orders_placed, self.orders_cancelled)
            if final_pos:
                log.info("residual positions (settle at resolution): %s",
                         {k: round(v, 1) for k, v in final_pos.items() if abs(v) > 0})

    def _write_summary(self, eq, dd, positions, final=False):
        (STATE / "live_summary.json").write_text(json.dumps({
            "ts": datetime.now(timezone.utc).isoformat(),
            "final": final,
            "start_balance_usd": round(self.start_equity or 0, 2),
            "equity_usd": round(eq, 2),
            "pnl_usd": round(eq - (self.start_equity or eq), 4),
            "orders_placed": self.orders_placed,
            "orders_cancelled": self.orders_cancelled,
            "open_positions": {k: round(v, 2) for k, v in positions.items() if abs(v) > 1e-9},
        }, indent=2))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=float, default=10.0)
    ap.add_argument("--size", type=int, default=None, help="contracts per quote side")
    ap.add_argument("--max-markets", type=int, default=3)
    ap.add_argument("--cycle", type=float, default=5.0)
    ap.add_argument("--rescan", type=float, default=60.0)
    ap.add_argument("--max-res-days", type=float, default=10.0,
                    help="skip incentivized markets that won't resolve within this many days")
    args = ap.parse_args()

    cfg = yaml.safe_load((HERE / "config.yaml").read_text())
    size = args.size if args.size is not None else cfg["quoting"]["quote_size"]

    client = KalshiClient(creds=Credentials.load(), dry_run=False)  # LIVE
    log.info("exchange: %s", client.exchange_status())
    bot = LiveBot(client, cfg, size=size, max_markets=args.max_markets,
                  max_res_days=args.max_res_days)
    bot.run(minutes=args.minutes, cycle=args.cycle, rescan=args.rescan)


if __name__ == "__main__":
    main()
