"""
Shadow market-making run. Quotes are VIRTUAL — no orders are ever placed.

    python main.py --hours 24 --capital 1000 --max-markets 5

Writes:
    state/shadow_summary.json   rolling summary (safe to cat anytime)
    state/shadow_events.jsonl   periodic snapshots
    state/shadow_run.log        human log

Stop early: touch state/HALT
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import time
from pathlib import Path

import yaml

from kalshi_client import KalshiClient
from inventory import Portfolio, RiskLimits
from quoter import QuoteConfig, compute_quotes
from scanner import is_two_sided, pull_open_markets, score as mm_score
from shadow_sim import ShadowPortfolio, SimAssumptions

HERE = Path(__file__).resolve().parent
STATE = HERE / "state"
STATE.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(message)s",
    handlers=[logging.StreamHandler(), logging.FileHandler(STATE / "shadow_run.log")],
)
log = logging.getLogger("shadow")


def parse_top_of_book(ob: dict):
    """Return (yes_bid_c, yes_bid_sz, yes_ask_c, yes_ask_sz) or None."""
    b = ob.get("orderbook_fp") or ob.get("orderbook") or {}
    yes = b.get("yes_dollars") or b.get("yes") or []
    no = b.get("no_dollars") or b.get("no") or []
    if not yes or not no:
        return None
    yb_price, yb_size = yes[-1]          # highest yes-bid level
    nb_price, nb_size = no[-1]           # highest no-bid level
    yes_bid_c = round(float(yb_price) * 100)
    yes_ask_c = round(100 - float(nb_price) * 100)
    return yes_bid_c, float(yb_size), yes_ask_c, float(nb_size)


def select_targets(client: KalshiClient, cfg: dict, max_markets: int) -> list[str]:
    q = cfg["quoting"]
    markets = pull_open_markets(client)
    real = [m for m in markets if is_two_sided(m)]
    # need a quotable spread and a real (non-empty) book
    real = [m for m in real
            if (float(m.get("yes_ask_dollars", 0)) - float(m.get("yes_bid_dollars", 0))) * 100
            >= q["min_spread_cents"]]
    real.sort(key=mm_score, reverse=True)
    return [m["ticker"] for m in real[: max_markets * 3]]  # over-select; book check filters later


def size_for_capital(capital: float, n_markets: int, mid_c: float) -> int:
    """~$1/contract-pair near mid; deploy ~half the per-market budget as resting size."""
    per_market = capital / max(1, n_markets)
    return max(1, int(per_market * 0.5))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=24.0)
    ap.add_argument("--capital", type=float, default=1000.0)
    ap.add_argument("--max-markets", type=int, default=5)
    ap.add_argument("--cycle", type=float, default=4.0, help="seconds per loop")
    ap.add_argument("--rescan", type=float, default=300.0, help="seconds between target rescans")
    args = ap.parse_args()

    cfg = yaml.safe_load((HERE / "config.yaml").read_text())
    qcfg_yaml = cfg["quoting"]

    # READ-ONLY client. dry_run=True means any accidental write is blocked, but we
    # never call an order method here at all.
    client = KalshiClient(dry_run=True)
    log.info("exchange: %s", client.exchange_status())

    assume = SimAssumptions()
    sp = ShadowPortfolio(assume=assume)
    # generous caps so position sizing is driven by capital, and the sim never
    # auto-halts (we want to MEASURE a full day, including drawdowns).
    limits = RiskLimits(max_position_per_market=10**9, max_total_exposure_usd=1e12,
                        max_daily_loss_usd=1e12, max_open_orders=10**6)
    pf = Portfolio(limits=limits)

    halt_file = STATE / "HALT"
    end_time = time.time() + args.hours * 3600
    targets: list[str] = []
    last_scan = 0.0
    last_cycle = time.time()
    last_summary = 0.0
    cycles = 0

    log.info("SHADOW RUN start: %.1fh, modeling $%.0f capital across <=%d markets. NO ORDERS.",
             args.hours, args.capital, args.max_markets)

    while time.time() < end_time:
        if halt_file.exists():
            log.warning("HALT file present — stopping.")
            break
        now = time.time()
        dt = now - last_cycle
        last_cycle = now

        # rescan targets periodically
        if now - last_scan > args.rescan or not targets:
            try:
                targets = select_targets(client, cfg, args.max_markets)
                log.info("rescan: %d candidate tickers", len(targets))
            except Exception as e:
                log.warning("rescan failed: %s", e)
            last_scan = now

        quoted = 0
        for ticker in targets:
            if quoted >= args.max_markets:
                break
            try:
                ob = client.get_orderbook(ticker, depth=5)
            except Exception:
                continue
            top = parse_top_of_book(ob)
            if not top:
                continue
            yes_bid_c, yes_bid_sz, yes_ask_c, yes_ask_sz = top
            if yes_ask_c - yes_bid_c < qcfg_yaml["min_spread_cents"]:
                continue
            quoted += 1

            ms = sp.m(ticker)
            mid = (yes_bid_c + yes_ask_c) / 2.0
            ms.last_mid = mid
            size = size_for_capital(args.capital, args.max_markets, mid)

            qc = QuoteConfig(
                quote_size=size,
                edge_cents=qcfg_yaml["edge_cents"],
                min_spread_cents=qcfg_yaml["min_spread_cents"],
                inventory_skew_cents=qcfg_yaml["inventory_skew_cents"],
            )
            intents = compute_quotes(ticker, yes_bid_c, yes_ask_c, mid, pf, qc)

            bid = ask = None
            for it in intents:
                if it.side == "yes":
                    bid = (float(it.price_cents), float(it.count))
                else:
                    ask = (float(100 - it.price_cents), float(it.count))  # back to yes-equiv
            ms.set_quotes(bid, ask)

            # are we at/inside the touch?
            our_at_bid = bid is not None and bid[0] >= yes_bid_c
            our_at_ask = ask is not None and ask[0] <= yes_ask_c
            ms.accrue(dt, yes_bid_sz, yes_ask_sz, our_at_bid, our_at_ask)

            # fills from real trades since last seen
            try:
                trades = client._request(  # public trades endpoint
                    "GET", "/markets/trades", params={"ticker": ticker, "limit": 50}, signed=False
                ).get("trades", [])
            except Exception:
                trades = []
            new = [t for t in trades if t.get("created_time", "") > ms.last_trade_ts]
            for t in sorted(new, key=lambda x: x.get("created_time", "")):
                yp = round(float(t["yes_price_dollars"]) * 100)
                cnt = float(t["count_fp"])
                ms.process_trade(
                    yes_price=yp, count=cnt, taker_side=t.get("taker_side", ""),
                    our_at_bid=our_at_bid, our_at_ask=our_at_ask,
                    size_ahead_bid=yes_bid_sz, size_ahead_ask=yes_ask_sz,
                    fee_cents_per=assume.maker_fee_cents_per_contract,
                )
            if new:
                ms.last_trade_ts = max(t.get("created_time", "") for t in new)

        cycles += 1

        # rolling summary
        if now - last_summary > 60:
            s = sp.summary()
            s["meta"] = {
                "elapsed_hours": round((args.hours * 3600 - (end_time - now)) / 3600, 3),
                "cycles": cycles,
                "active_targets": quoted,
                "capital_modeled": args.capital,
                "note": "est_reward uses ASSUMED pools (low/exp/high $10/$50/$200 per market/day). "
                        "inventory_pnl is measured. NO real orders.",
            }
            (STATE / "shadow_summary.json").write_text(json.dumps(s, indent=2))
            with (STATE / "shadow_events.jsonl").open("a") as f:
                f.write(json.dumps({"t": now, **s["meta"],
                                    "net_exp": s["net_usd_expected"],
                                    "inv_pnl": s["inventory_pnl_usd"],
                                    "reward_exp": s["est_reward_usd_expected"],
                                    "fills": s["total_fills"],
                                    "avg_share_pct": s["avg_share_pct"]}) + "\n")
            log.info("t=%.2fh quoted=%d fills=%d share=%.1f%% inv_pnl=$%.3f reward_exp=$%.3f net_exp=$%.3f",
                     s["meta"]["elapsed_hours"], quoted, s["total_fills"], s["avg_share_pct"],
                     s["inventory_pnl_usd"], s["est_reward_usd_expected"], s["net_usd_expected"])
            last_summary = now

        time.sleep(max(0.0, args.cycle - (time.time() - now)))

    # final report
    s = sp.summary()
    (STATE / "shadow_summary.json").write_text(json.dumps(s, indent=2))
    log.info("=== SHADOW RUN COMPLETE ===")
    log.info(json.dumps({k: v for k, v in s.items() if k != "per_market"}, indent=2))
    log.info("Full per-market breakdown in state/shadow_summary.json")


if __name__ == "__main__":
    main()
