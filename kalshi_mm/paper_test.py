"""
Paper test of the REDESIGNED strategy on live data — ZERO money.

Runs the exact redesigned decision logic (safety-first target selection + two-sided
quotes + per-position stop-loss) against the real order book and real trade prints,
simulating fills, and reports the net result. This is the gate before any more real
money: if net isn't clearly positive here, we don't fund.

    python paper_test.py --minutes 60 --size 20 --max-markets 3 --stop-cents 4

Writes state/paper_summary.json. Stop early: touch state/HALT
"""

from __future__ import annotations

import argparse
import json
import logging
import time
from pathlib import Path

import yaml

from kalshi_client import Credentials, KalshiClient
from incentives import parse_top, select_targets
from inventory import Portfolio, RiskLimits
from quoter import QuoteConfig, compute_quotes
from shadow_sim import ShadowPortfolio, SimAssumptions

HERE = Path(__file__).resolve().parent
STATE = HERE / "state"
STATE.mkdir(exist_ok=True)
HALT = STATE / "HALT"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s",
                    handlers=[logging.StreamHandler(), logging.FileHandler(STATE / "paper_run.log")])
log = logging.getLogger("paper")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=float, default=60.0)
    ap.add_argument("--size", type=int, default=20)
    ap.add_argument("--max-markets", type=int, default=3)
    ap.add_argument("--cycle", type=float, default=5.0)
    ap.add_argument("--rescan", type=float, default=90.0)
    ap.add_argument("--stop-cents", type=float, default=4.0, help="per-position adverse-move stop")
    ap.add_argument("--permissive", action="store_true",
                    help="loosen selection to engage the actual (thin/wide) incentivized markets, "
                         "relying on the stop-loss for protection — to measure real net")
    ap.add_argument("--min-spread", type=int, default=None, help="override quoter min spread")
    args = ap.parse_args()

    cfg = yaml.safe_load((HERE / "config.yaml").read_text())["quoting"]
    # creds needed for the authenticated /incentive_programs read; dry_run blocks ALL writes
    client = KalshiClient(creds=Credentials.load(), dry_run=True)
    log.info("exchange: %s", client.exchange_status())

    sp = ShadowPortfolio(assume=SimAssumptions())
    pf = Portfolio(limits=RiskLimits(max_position_per_market=args.size * 2,
                                     max_total_exposure_usd=1e9, max_daily_loss_usd=1e9,
                                     max_open_orders=10**6))
    end = time.time() + args.minutes * 60
    targets = []
    last_scan = last_log = last_cycle = 0.0
    last_cycle = time.time()
    stop_events = 0
    min_equity = 0.0  # track worst drawdown of inventory pnl

    log.info("PAPER start: redesigned strategy, size=%d max_markets=%d stop=%.0fc. NO MONEY.",
             args.size, args.max_markets, args.stop_cents)

    while time.time() < end:
        if HALT.exists():
            log.warning("HALT -> stop")
            break
        now = time.time()
        dt = now - last_cycle
        last_cycle = now
        try:
            min_spread = args.min_spread if args.min_spread is not None else cfg["min_spread_cents"]
            if now - last_scan > args.rescan or not targets:
                if args.permissive:
                    targets = select_targets(client, args.size, args.max_markets, min_spread,
                                             max_spread_cents=40, price_lo=8, price_hi=92,
                                             min_resolution_hours=2, max_resolution_days=400,
                                             min_exit_depth=args.size, max_recent_move_cents=20)
                else:
                    targets = select_targets(client, args.size, args.max_markets, min_spread)
                log.info("targets: %s", [f"{t.ticker}({t.yes_bid}/{t.yes_ask},depth={t.bid_size:.0f}/{t.ask_size:.0f})"
                                         for t in targets])
                last_scan = now

            for t in targets:
                try:
                    top = parse_top(client.get_orderbook(t.ticker, depth=3))
                except Exception:
                    continue
                if not top:
                    continue
                yb, ybs, ya, yas = top
                ms = sp.m(t.ticker)
                ms.last_mid = (yb + ya) / 2

                # per-position stop BEFORE re-quoting
                if ms.stop_flatten(args.stop_cents, yb, ya):
                    stop_events += 1
                    log.info("STOP-FLATTEN %s realized now $%.2f", t.ticker, ms.realized_cents / 100)

                size = args.size
                qc = QuoteConfig(quote_size=size, edge_cents=cfg["edge_cents"],
                                 min_spread_cents=min_spread,
                                 inventory_skew_cents=cfg["inventory_skew_cents"])
                intents = compute_quotes(t.ticker, yb, ya, ms.last_mid, pf, qc)
                bid = ask = None
                for it in intents:
                    if it.side == "yes":
                        bid = (float(it.price_cents), float(it.count))
                    else:
                        ask = (float(100 - it.price_cents), float(it.count))
                ms.set_quotes(bid, ask)

                our_at_bid = bid is not None and bid[0] >= yb
                our_at_ask = ask is not None and ask[0] <= ya
                ms.accrue(dt, ybs, yas, our_at_bid, our_at_ask)

                # simulate fills from real trades
                try:
                    trades = client._request("GET", "/markets/trades",
                                             params={"ticker": t.ticker, "limit": 50}, signed=False).get("trades", [])
                except Exception:
                    trades = []
                new = [x for x in trades if x.get("created_time", "") > ms.last_trade_ts]
                for x in sorted(new, key=lambda z: z.get("created_time", "")):
                    ms.process_trade(round(float(x["yes_price_dollars"]) * 100), float(x["count_fp"]),
                                     x.get("taker_side", ""), our_at_bid, our_at_ask, ybs, yas, 0.0)
                if new:
                    ms.last_trade_ts = max(x.get("created_time", "") for x in new)

            inv = sp.inventory_pnl_usd()
            min_equity = min(min_equity, inv)
            if now - last_log > 30:
                s = sp.summary()
                net = s["net_usd_expected"]
                log.info("t=%.1fmin fills=%d stops=%d inv_pnl=$%.2f worst=$%.2f reward_exp=$%.3f net_exp=$%.3f",
                         (args.minutes * 60 - (end - now)) / 60, s["total_fills"], stop_events,
                         inv, min_equity, s["est_reward_usd_expected"], net)
                s["meta"] = {"stop_events": stop_events, "worst_inventory_pnl_usd": round(min_equity, 2),
                             "stop_cents": args.stop_cents}
                (STATE / "paper_summary.json").write_text(json.dumps(s, indent=2))
                last_log = now
        except Exception as e:
            log.warning("cycle error (continuing): %s", e)
        time.sleep(max(0.0, args.cycle - (time.time() - now)))

    s = sp.summary()
    s["meta"] = {"stop_events": stop_events, "worst_inventory_pnl_usd": round(min_equity, 2)}
    (STATE / "paper_summary.json").write_text(json.dumps(s, indent=2))
    log.info("=== PAPER DONE. inv_pnl=$%.2f worst=$%.2f reward_exp=$%.3f NET_exp=$%.3f fills=%d stops=%d ===",
             sp.inventory_pnl_usd(), min_equity, sp.est_reward_usd("expected"),
             sp.net_usd("expected"), s["total_fills"], stop_events)


if __name__ == "__main__":
    main()
