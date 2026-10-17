#!/usr/bin/env python3
from __future__ import annotations

import argparse
import concurrent.futures
import datetime as dt
import heapq
import time
from collections import defaultdict
from typing import Any

from common import RESULTS, as_float, dump_json, fee, get_json, quote_from_market


def side_ask(market: dict[str, Any], side: str) -> float | None:
    quote = quote_from_market(market, side)
    return quote[1] if quote else None


def opposite(side: str) -> str:
    return "no" if side == "yes" else "yes"


def normalized_legs(market: dict[str, Any]) -> tuple[tuple[str, str], ...]:
    return tuple(sorted((str(leg["market_ticker"]), str(leg["side"])) for leg in market.get("mve_selected_legs") or []))


def book_ask(ticker: str, side: str) -> tuple[float, float] | None:
    payload = get_json(f"/markets/{ticker}/orderbook", {"depth": 25})
    book = payload.get("orderbook_fp") or payload.get("orderbook") or {}
    if side == "yes":
        opposing = book.get("no_dollars") or book.get("no") or []
    else:
        opposing = book.get("yes_dollars") or book.get("yes") or []
    levels: list[tuple[float, float]] = []
    for level in opposing:
        if isinstance(level, dict):
            price = as_float(level.get("price_dollars", level.get("price")))
            size = as_float(level.get("quantity_fp", level.get("quantity")))
        else:
            price = as_float(level[0]) if len(level) > 1 else None
            size = as_float(level[1]) if len(level) > 1 else None
        if price is not None and size is not None and size > 0:
            levels.append((price, size))
    if not levels:
        return None
    bid, size = max(levels)
    return 1.0 - bid, size


def verify_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
    legs = candidate["orders"]
    verified: list[dict[str, Any]] = []
    for order in legs:
        quote = book_ask(order["ticker"], order["side"])
        if quote is None:
            return {**candidate, "verified": False, "verify_reason": "missing_depth", "verified_orders": verified}
        ask, size = quote
        verified.append({**order, "ask": ask, "size": size})
    total_cost = sum(order["ask"] + fee(order["ask"]) for order in verified)
    guaranteed_payout = float(candidate["guaranteed_payout"])
    return {
        **candidate,
        "verified": min(order["size"] for order in verified) >= 1.0 and total_cost < guaranteed_payout,
        "verified_orders": verified,
        "verified_cost_after_fees": total_cost,
        "verified_profit": guaranteed_payout - total_cost,
        "verified_capacity": min(order["size"] for order in verified),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--verify-limit", type=int, default=100)
    parser.add_argument("--lookback-hours", type=int, default=12)
    parser.add_argument("--max-markets", type=int, default=50000)
    args = parser.parse_args()

    cutoff = int((dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=args.lookback_hours)).timestamp())
    # The full MVE namespace contains a very large number of dynamically created,
    # unquoted contracts. Stream pages and retain only currently quoted combos.
    mves: list[dict[str, Any]] = []
    total_mves = 0
    leg_tickers: set[str] = set()
    cursor = ""
    truncated = False
    while True:
        params: dict[str, Any] = {
            "status": "open",
            "mve_filter": "only",
            "min_created_ts": cutoff,
            "limit": 1000,
        }
        if cursor:
            params["cursor"] = cursor
        payload = get_json("/markets", params)
        page = payload.get("markets") or []
        total_mves += len(page)
        if total_mves % 5000 == 0:
            print(f"Scanned {total_mves:,} open MVE markets...", flush=True)
        for market in page:
            legs = normalized_legs(market)
            if not legs:
                continue
            if quote_from_market(market, "yes") is None and quote_from_market(market, "no") is None:
                continue
            mves.append(market)
            leg_tickers.update(ticker for ticker, _ in legs)
        cursor = str(payload.get("cursor") or "")
        if total_mves >= args.max_markets and cursor:
            truncated = True
            break
        if not cursor:
            break
        time.sleep(0.15)

    base_markets: list[dict[str, Any]] = []
    ordered_leg_tickers = sorted(leg_tickers)
    for start in range(0, len(ordered_leg_tickers), 100):
        batch = ordered_leg_tickers[start : start + 100]
        payload = get_json(
            "/markets",
            {"tickers": ",".join(batch), "mve_filter": "exclude", "limit": 1000},
        )
        base_markets.extend(payload.get("markets") or [])
        time.sleep(0.15)
    by_ticker = {market["ticker"]: market for market in base_markets}
    by_ticker.update({market["ticker"]: market for market in mves})

    candidates: list[dict[str, Any]] = []
    nearest: list[tuple[float, int, dict[str, Any]]] = []
    nearest_counter = 0
    coverage = defaultdict(int)
    duplicates: dict[tuple[tuple[str, str], ...], list[dict[str, Any]]] = defaultdict(list)

    def track_nearest(row: dict[str, Any]) -> None:
        nonlocal nearest_counter
        nearest_counter += 1
        item = (-float(row["snapshot_cost_after_fees"]), nearest_counter, row)
        heapq.heappush(nearest, item)
        if len(nearest) > 100:
            heapq.heappop(nearest)

    for combo in mves:
        legs = normalized_legs(combo)
        coverage["mve_markets"] += 1
        if not legs:
            coverage["missing_leg_metadata"] += 1
            continue
        duplicates[legs].append(combo)
        resolved: list[tuple[str, str, dict[str, Any]]] = []
        for ticker, side in legs:
            market = by_ticker.get(ticker)
            if market is None:
                coverage["missing_constituent_market"] += 1
                resolved = []
                break
            resolved.append((ticker, side, market))
        if not resolved:
            continue
        coverage["resolved_combos"] += 1

        combo_no = side_ask(combo, "no")
        if combo_no is not None:
            for ticker, side, leg_market in resolved:
                leg_ask = side_ask(leg_market, side)
                if leg_ask is None:
                    continue
                raw_cost = combo_no + leg_ask
                cost_after_fees = raw_cost + fee(combo_no) + fee(leg_ask)
                near_row = {
                    "kind": "upper_bound",
                    "combo": combo["ticker"],
                    "leg": ticker,
                    "leg_count": len(legs),
                    "snapshot_raw_cost": raw_cost,
                    "snapshot_cost_after_fees": cost_after_fees,
                    "snapshot_profit": 1.0 - cost_after_fees,
                }
                track_nearest(near_row)
                if cost_after_fees < 1.0:
                    candidates.append({
                        "kind": "upper_bound",
                        "combo": combo["ticker"],
                        "leg_count": len(legs),
                        "guaranteed_payout": 1.0,
                        "snapshot_cost_after_fees": cost_after_fees,
                        "snapshot_profit": 1.0 - cost_after_fees,
                        "orders": [
                            {"ticker": combo["ticker"], "side": "no", "ask": combo_no},
                            {"ticker": ticker, "side": side, "ask": leg_ask},
                        ],
                    })

        combo_yes = side_ask(combo, "yes")
        opposite_orders: list[dict[str, Any]] = []
        if combo_yes is not None:
            for ticker, side, leg_market in resolved:
                ask = side_ask(leg_market, opposite(side))
                if ask is None:
                    opposite_orders = []
                    break
                opposite_orders.append({"ticker": ticker, "side": opposite(side), "ask": ask})
        if combo_yes is not None and opposite_orders:
            orders = [{"ticker": combo["ticker"], "side": "yes", "ask": combo_yes}, *opposite_orders]
            cost_after_fees = sum(order["ask"] + fee(order["ask"]) for order in orders)
            track_nearest({
                "kind": "lower_bound",
                "combo": combo["ticker"],
                "leg_count": len(legs),
                "snapshot_raw_cost": sum(order["ask"] for order in orders),
                "snapshot_cost_after_fees": cost_after_fees,
                "snapshot_profit": 1.0 - cost_after_fees,
            })
            if cost_after_fees < 1.0:
                candidates.append({
                    "kind": "lower_bound",
                    "combo": combo["ticker"],
                    "leg_count": len(legs),
                    "guaranteed_payout": 1.0,
                    "snapshot_cost_after_fees": cost_after_fees,
                    "snapshot_profit": 1.0 - cost_after_fees,
                    "orders": orders,
                })

    for legs, markets in duplicates.items():
        if len(markets) < 2:
            continue
        coverage["duplicate_leg_sets"] += 1
        for cheap in markets:
            cheap_yes = side_ask(cheap, "yes")
            if cheap_yes is None:
                continue
            for dear in markets:
                if cheap["ticker"] == dear["ticker"]:
                    continue
                dear_no = side_ask(dear, "no")
                if dear_no is None:
                    continue
                cost_after_fees = cheap_yes + fee(cheap_yes) + dear_no + fee(dear_no)
                track_nearest({
                    "kind": "duplicate_combo",
                    "combo": cheap["ticker"],
                    "other_combo": dear["ticker"],
                    "leg_count": len(legs),
                    "snapshot_raw_cost": cheap_yes + dear_no,
                    "snapshot_cost_after_fees": cost_after_fees,
                    "snapshot_profit": 1.0 - cost_after_fees,
                })
                if cost_after_fees < 1.0:
                    candidates.append({
                        "kind": "duplicate_combo",
                        "combo": cheap["ticker"],
                        "other_combo": dear["ticker"],
                        "leg_count": len(legs),
                        "guaranteed_payout": 1.0,
                        "snapshot_cost_after_fees": cost_after_fees,
                        "snapshot_profit": 1.0 - cost_after_fees,
                        "orders": [
                            {"ticker": cheap["ticker"], "side": "yes", "ask": cheap_yes},
                            {"ticker": dear["ticker"], "side": "no", "ask": dear_no},
                        ],
                    })

    candidates.sort(key=lambda row: row["snapshot_profit"], reverse=True)
    to_verify = candidates[: args.verify_limit]
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        verified = list(executor.map(verify_candidate, to_verify))
    payload = {
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "coverage": dict(coverage),
        "open_mve_markets_scanned": total_mves,
        "scan_truncated_at_cap": truncated,
        "quoted_mve_markets": len(mves),
        "base_markets": len(base_markets),
        "snapshot_candidates": len(candidates),
        "verified_candidates": sum(bool(row.get("verified")) for row in verified),
        "nearest_bounds": [row for _, _, row in sorted(nearest, key=lambda item: -item[0])],
        "candidates": verified + candidates[len(to_verify):],
    }
    dump_json(RESULTS / "combo_scan.json", payload)
    print(f"Open MVE markets scanned: {total_mves:,}")
    print(f"Quoted MVE markets: {len(mves):,}")
    print(f"Resolved combos: {coverage['resolved_combos']:,}")
    print(f"Snapshot candidates: {len(candidates):,}")
    print(f"Depth-verified candidates: {payload['verified_candidates']:,}")
    if payload["nearest_bounds"]:
        closest = payload["nearest_bounds"][0]
        print("Closest after-fee bound:", closest["kind"], f"cost={closest['snapshot_cost_after_fees']:.4f}")
    for row in verified[:10]:
        print(row["kind"], f"profit={row.get('verified_profit', 0):.4f}", row["combo"], row.get("verify_reason", ""))


if __name__ == "__main__":
    main()
