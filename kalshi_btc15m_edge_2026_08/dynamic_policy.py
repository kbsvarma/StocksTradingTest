#!/usr/bin/env python3
"""Discovery-only Markov optimal-stopping policy for BTC15M."""

from __future__ import annotations

import math
import statistics
from collections import Counter, defaultdict

import scalp_study
import study


MIN_STATE_COUNT = 50


def state_key(market: study.Market, remainder: int) -> tuple | None:
    current = study.yes_quote(market.by_rem.get(remainder) or {})
    prior = study.yes_quote(market.by_rem.get(remainder + 1) or {})
    if current is None or prior is None:
        return None
    bid, ask = current
    mid = (bid + ask) / 2
    prior_mid = sum(prior) / 2
    momentum = -1 if mid - prior_mid < -0.02 else 1 if mid - prior_mid > 0.02 else 0
    price_bin = min(int(mid / 0.05), 19)
    tight = ask - bid <= 0.01 + study.EPSILON
    return remainder, price_bin, momentum, tight


def discovery_model(markets: list[study.Market]) -> dict:
    state_rows = defaultdict(list)
    transitions = defaultdict(Counter)
    outcomes = defaultdict(list)
    for market in markets:
        if market.close_ts >= study.VALIDATION_START:
            continue
        for remainder in range(13, 0, -1):
            key = state_key(market, remainder)
            if key is None:
                continue
            quote = study.yes_quote(market.by_rem.get(remainder) or {})
            state_rows[key].append(quote)
            outcomes[key].append(market.result == "yes")
            if remainder > 1:
                next_key = state_key(market, remainder - 1)
                if next_key is not None:
                    transitions[key][next_key] += 1
    usable = {key for key, rows in state_rows.items() if len(rows) >= MIN_STATE_COUNT}
    representative = {
        key: (
            statistics.mean(row[0] for row in state_rows[key]),
            statistics.mean(row[1] for row in state_rows[key]),
        )
        for key in usable
    }
    probabilities = {
        key: (sum(outcomes[key]) + 1) / (len(outcomes[key]) + 2)
        for key in usable
    }
    normalized_transitions = {}
    for key in usable:
        valid = {target: count for target, count in transitions[key].items() if target in usable}
        total = sum(valid.values())
        if total:
            normalized_transitions[key] = {
                target: count / total for target, count in valid.items()
            }
    return {
        "usable": usable,
        "quotes": representative,
        "p_yes": probabilities,
        "transitions": normalized_transitions,
        "counts": {key: len(state_rows[key]) for key in usable},
    }


def expected(values: dict, probabilities: dict) -> float | None:
    available = [(target, weight) for target, weight in probabilities.items() if target in values]
    total = sum(weight for _, weight in available)
    if total == 0:
        return None
    return sum(values[target] * weight for target, weight in available) / total


def solve(model: dict) -> dict:
    flat_value = {}
    yes_value = {}
    no_value = {}
    flat_action = {}
    yes_action = {}
    no_action = {}

    for remainder in range(1, 14):
        keys = [key for key in model["usable"] if key[0] == remainder]
        for key in keys:
            bid, ask = model["quotes"][key]
            no_bid, no_ask = 1 - ask, 1 - bid
            if remainder == 1:
                continuation_yes = model["p_yes"][key]
                continuation_no = 1 - model["p_yes"][key]
                wait = 0.0
            else:
                transition = model["transitions"].get(key) or {}
                continuation_yes = expected(yes_value, transition)
                continuation_no = expected(no_value, transition)
                wait = expected(flat_value, transition)
                if continuation_yes is None or continuation_no is None or wait is None:
                    continue

            sell_yes = bid - study.fee(bid)
            sell_no = no_bid - study.fee(no_bid)
            yes_choices = {"hold": continuation_yes, "sell": sell_yes}
            no_choices = {"hold": continuation_no, "sell": sell_no}
            yes_action[key], yes_value[key] = max(yes_choices.items(), key=lambda item: item[1])
            no_action[key], no_value[key] = max(no_choices.items(), key=lambda item: item[1])

            buy_yes = -ask - study.fee(ask) + continuation_yes
            buy_no = -no_ask - study.fee(no_ask) + continuation_no
            flat_choices = {"wait": max(wait, 0.0), "buy_yes": buy_yes, "buy_no": buy_no}
            flat_action[key], flat_value[key] = max(flat_choices.items(), key=lambda item: item[1])

    return {
        "flat_action": flat_action,
        "yes_action": yes_action,
        "no_action": no_action,
        "flat_value": flat_value,
    }


def evaluate(markets: list[study.Market], policy: dict) -> list[study.Trade]:
    trades = []
    for market in markets:
        if market.close_ts < study.VALIDATION_START:
            continue
        holding = None
        entry = None
        entry_rem = None
        pnl = None
        for remainder in range(13, 0, -1):
            key = state_key(market, remainder)
            if key is None:
                continue
            if holding is None:
                action = policy["flat_action"].get(key, "wait")
                if action not in {"buy_yes", "buy_no"}:
                    continue
                side = "yes" if action == "buy_yes" else "no"
                executable = study.executable_entry(market, remainder, side)
                if executable is None:
                    continue
                holding, entry, entry_rem = side, executable, remainder
                continue

            action_map = policy["yes_action"] if holding == "yes" else policy["no_action"]
            if action_map.get(key) != "sell":
                continue
            quote = study.side_quote(market.by_rem.get(remainder) or {}, holding)
            if quote is None:
                continue
            exit_bid = quote[0]
            pnl = exit_bid - entry - study.fee(entry) - study.fee(exit_bid)
            break

        if holding is None:
            continue
        if pnl is None:
            pnl = study.settlement_pnl(entry, market.result == holding)
        trades.append(
            study.Trade(
                "dynamic_policy", market.ticker, market.close_ts, holding,
                entry, pnl, entry_rem,
            )
        )
    return trades


def main() -> None:
    markets = study.load_markets()
    model = discovery_model(markets)
    policy = solve(model)
    trades = evaluate(markets, policy)
    print(
        f"usable discovery states={len(model['usable'])} "
        f"flat decisions={len(policy['flat_action'])} "
        f"buy states={sum(action.startswith('buy') for action in policy['flat_action'].values())}"
    )
    for period, rows in scalp_study.periods(trades):
        print(study.summarize(period, rows))
    final = study.segment(trades, scalp_study.FINAL_START)
    print("final actions by entry minute:", dict(Counter(t.signal_rem for t in final)))
    if final:
        print(
            f"final bootstrap25={study.bootstrap_days(final, .25)} "
            f"day-p={scalp_study.day_pvalue(final):.4f}"
        )


if __name__ == "__main__":
    main()
