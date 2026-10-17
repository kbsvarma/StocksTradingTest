#!/usr/bin/env python3
"""Late-window forced-allocation and maker-first execution study.

Every policy allocates exactly once per aligned BTC/ETH window.  Maker results
are explicitly labelled as quote-touch bounds because one-minute quote candles
cannot prove queue position or an actual fill.
"""

from __future__ import annotations

import json
import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Sequence

from forced_allocator_study import (
    OLD_DATA,
    RECENT_DATA,
    SERIES,
    AssetState,
    Choice,
    PairState,
    bootstrap,
    candidate,
    fee,
    load_markets,
    load_pairs,
    path_features,
    quote_map,
    replay_policy,
    side_quotes,
    split_days,
    summarize,
)


HERE = Path(__file__).resolve().parent
OUT = HERE / "out"
POLICIES = (
    "btc_favorite",
    "eth_favorite",
    "strongest_favorite",
    "cheapest_favorite",
    "market_prior",
)
HALF_LIVES = (96, 192, 384, 672, 1344)


@dataclass(frozen=True)
class MakerContext:
    pair: PairState
    btc_market: object
    rem_one: AssetState
    rem_two: AssetState


def favorite_side(state: AssetState) -> str:
    return "yes" if state.mid >= 0.5 else "no"


def fixed_policy_results(rem: int, stress: float) -> dict[str, dict[str, dict]]:
    old = load_pairs(OLD_DATA, rem)
    recent = load_pairs(RECENT_DATA, rem)
    train, tune, validation = split_days(old)
    result: dict[str, dict[str, dict]] = {}
    for split, pairs in (
        ("train", train),
        ("tune", tune),
        ("validation", validation),
        ("recent", recent),
    ):
        result[split] = {}
        for policy in POLICIES:
            choices = replay_policy(pairs, None, policy, stress)
            result[split][policy] = summarize(
                choices,
                len(pairs),
                do_bootstrap=rem == 1 and policy in {"btc_favorite", "market_prior"},
            )
    return result


def choices_by_policy(pairs: Sequence[PairState], stress: float) -> dict[str, list[Choice]]:
    return {
        policy: replay_policy(pairs, None, policy, stress)
        for policy in POLICIES
    }


def online_expert_choices(
    pairs: Sequence[PairState], stress: float, half_life: int
) -> list[Choice]:
    """Choose the expert with the best prior exponentially weighted PnL.

    Outcomes for BTC and ETH are public after every interval, so all expert
    scores can be updated without counterfactual fill assumptions beyond the
    same replay model used for the selected action.
    """

    by_policy = choices_by_policy(pairs, stress)
    indexed = {
        policy: {choice.close_ts: choice for choice in choices}
        for policy, choices in by_policy.items()
    }
    scores = {policy: 0.0 for policy in POLICIES}
    decay = math.exp(math.log(0.5) / half_life)
    selected: list[Choice] = []
    for pair in sorted(pairs, key=lambda item: item.close_ts):
        available = [policy for policy in POLICIES if pair.close_ts in indexed[policy]]
        if not available:
            continue
        policy = max(available, key=lambda item: (scores[item], -POLICIES.index(item)))
        source = indexed[policy][pair.close_ts]
        selected.append(
            Choice(
                source.close_ts,
                source.day,
                f"online_expert_hl{half_life}",
                source.family,
                source.side,
                source.signal_price,
                source.fill_price,
                source.predicted_probability,
                source.predicted_ev,
                source.won,
                source.pnl,
            )
        )
        for expert in available:
            scores[expert] = decay * scores[expert] + indexed[expert][pair.close_ts].pnl
    return selected


def load_maker_contexts(data_dir: Path) -> list[MakerContext]:
    markets = {market.close_ts: market for market in load_markets(data_dir, SERIES[0])}
    pairs = {pair.close_ts: pair for pair in load_pairs(data_dir, 1)}
    contexts: list[MakerContext] = []
    for close_ts, pair in pairs.items():
        market = markets.get(close_ts)
        if market is None or pair.btc is None:
            continue
        rem_two = path_features(market, 2)
        if rem_two is None or 1 not in quote_map(market):
            continue
        contexts.append(MakerContext(pair, market, pair.btc, rem_two))
    return contexts


def maker_replay(
    contexts: Sequence[MakerContext],
    *,
    level: str,
    fill_rule: str,
    taker_stress: float,
) -> tuple[list[Choice], dict[str, float]]:
    """Rest at rem=2, then guarantee allocation with a rem=1 taker.

    ``touch`` assumes a fill when the following candle's displayed ask reaches
    the limit. ``through`` requires the ask to move a full cent through the
    limit. Neither proves queue position; they bound plausible maker capture.
    """

    choices: list[Choice] = []
    fills = 0
    leader_changes = 0
    for context in contexts:
        pair = context.pair
        market = context.btc_market
        rem_one = context.rem_one
        rem_two = context.rem_two
        side_two = favorite_side(rem_two)
        side_one = favorite_side(rem_one)
        leader_changes += side_two != side_one

        mapped = quote_map(market)
        two_index = mapped[2][0]
        one_index = mapped[1][0]
        at_two = side_quotes(market.candles[two_index], side_two)
        during_rest = side_quotes(market.candles[one_index], side_two)
        if at_two is None or during_rest is None:
            continue

        bid = at_two["bid_close"]
        ask = at_two["ask_close"]
        if level == "best_bid":
            limit = bid
        elif level == "ask_minus_1c":
            limit = max(bid, ask - 0.01)
        else:
            raise ValueError(f"unknown maker level: {level}")
        limit = min(limit, ask - 0.001)
        threshold = limit if fill_rule == "touch" else limit - 0.01
        maker_filled = during_rest["ask_low"] <= threshold + 1e-9

        if maker_filled:
            fills += 1
            won = (rem_two.result_yes == 1) == (side_two == "yes")
            pnl = (1.0 if won else 0.0) - limit - fee(limit)
            choices.append(
                Choice(
                    pair.close_ts,
                    pair.day,
                    f"maker_{level}_{fill_rule}",
                    SERIES[0],
                    side_two,
                    limit,
                    limit,
                    max(rem_two.mid, 1.0 - rem_two.mid),
                    0.0,
                    won,
                    pnl,
                )
            )
            continue

        values = candidate(rem_one, side_one, rem_one.mid, taker_stress)
        signal, fill, probability, predicted_ev, won, pnl = values
        choices.append(
            Choice(
                pair.close_ts,
                pair.day,
                "deadline_taker",
                SERIES[0],
                side_one,
                signal,
                fill,
                probability,
                predicted_ev,
                won,
                pnl,
            )
        )
    diagnostics = {
        "maker_fill_rate_bound": fills / len(choices) if choices else 0.0,
        "leader_change_rate": leader_changes / len(contexts) if contexts else 0.0,
    }
    return choices, diagnostics


def cents(value: float) -> str:
    return f"{100 * value:+.3f}c"


def run() -> dict:
    fixed = {
        str(rem): fixed_policy_results(rem, stress=0.01)
        for rem in (8, 6, 4, 2, 1)
    }

    old_rem_one = load_pairs(OLD_DATA, 1)
    recent_rem_one = load_pairs(RECENT_DATA, 1)
    train, tune, validation = split_days(old_rem_one)
    expert: dict[str, dict[str, dict]] = defaultdict(dict)
    for half_life in HALF_LIVES:
        for split, pairs in (
            ("train", train),
            ("tune", tune),
            ("validation", validation),
            ("recent", recent_rem_one),
        ):
            choices = online_expert_choices(pairs, 0.01, half_life)
            expert[str(half_life)][split] = summarize(
                choices, len(pairs), do_bootstrap=split in {"validation", "recent"}
            )

    old_contexts = load_maker_contexts(OLD_DATA)
    recent_contexts = load_maker_contexts(RECENT_DATA)
    day_sets = {
        "train": {pair.day for pair in train},
        "tune": {pair.day for pair in tune},
        "validation": {pair.day for pair in validation},
    }
    maker: dict[str, dict[str, dict]] = defaultdict(dict)
    for level in ("best_bid", "ask_minus_1c"):
        for fill_rule in ("touch", "through"):
            key = f"{level}_{fill_rule}"
            for split, contexts in (
                ("train", [row for row in old_contexts if row.pair.day in day_sets["train"]]),
                ("tune", [row for row in old_contexts if row.pair.day in day_sets["tune"]]),
                ("validation", [row for row in old_contexts if row.pair.day in day_sets["validation"]]),
                ("recent", recent_contexts),
            ):
                choices, diagnostics = maker_replay(
                    contexts,
                    level=level,
                    fill_rule=fill_rule,
                    taker_stress=0.01,
                )
                stats = summarize(
                    choices,
                    len(contexts),
                    do_bootstrap=split in {"validation", "recent"},
                )
                maker[key][split] = {**stats, **diagnostics}

    return {"fixed": fixed, "expert": dict(expert), "maker": dict(maker)}


def render(result: dict) -> str:
    lines = [
        "# Late BTC/ETH Forced-Allocation Study",
        "",
        f"Generated: {datetime.now(timezone.utc).isoformat()}",
        "",
        "Every row allocates one position per executable window. Results include exact fees and a +1c taker-entry stress.",
        "",
        "## Fixed Rules By Entry Time",
        "",
        "| Rem | Policy | Train | Tune | Validation | Recent |",
        "|---:|---|---:|---:|---:|---:|",
    ]
    for rem in (8, 6, 4, 2, 1):
        values = result["fixed"][str(rem)]
        for policy in ("btc_favorite", "market_prior", "strongest_favorite"):
            lines.append(
                f"| {rem} | {policy} | {cents(values['train'][policy]['ev'])} | "
                f"{cents(values['tune'][policy]['ev'])} | {cents(values['validation'][policy]['ev'])} | "
                f"{cents(values['recent'][policy]['ev'])} |"
            )

    lines.extend(
        [
            "",
            "## Adaptive Expert Allocator",
            "",
            "| Half-life | Train | Tune | Validation | Recent | Validation p05 | Recent p05 |",
            "|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for half_life in HALF_LIVES:
        row = result["expert"][str(half_life)]
        lines.append(
            f"| {half_life} | {cents(row['train']['ev'])} | {cents(row['tune']['ev'])} | "
            f"{cents(row['validation']['ev'])} | {cents(row['recent']['ev'])} | "
            f"{cents(row['validation'].get('p05', 0.0))} | {cents(row['recent'].get('p05', 0.0))} |"
        )

    lines.extend(
        [
            "",
            "## Maker-First, Taker-Deadline Bounds",
            "",
            "The order rests from rem=2 to rem=1. If it does not fill, the bot takes the current BTC favorite at rem=1, so coverage remains 100%. Quote candles cannot prove maker fills; `touch` is optimistic and `through` is the stricter bound.",
            "",
            "| Rule | Train | Tune | Validation | Recent | Val fill bound | Recent fill bound | Val p05 | Recent p05 |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for key, row in result["maker"].items():
        lines.append(
            f"| {key} | {cents(row['train']['ev'])} | {cents(row['tune']['ev'])} | "
            f"{cents(row['validation']['ev'])} | {cents(row['recent']['ev'])} | "
            f"{row['validation']['maker_fill_rate_bound']:.1%} | {row['recent']['maker_fill_rate_bound']:.1%} | "
            f"{cents(row['validation'].get('p05', 0.0))} | {cents(row['recent'].get('p05', 0.0))} |"
        )

    btc = result["fixed"]["1"]
    lines.extend(
        [
            "",
            "## Verdict",
            "",
            f"The cleanest forced rule is **BTC favorite at rem=1**: validation {cents(btc['validation']['btc_favorite']['ev'])} and recent {cents(btc['recent']['btc_favorite']['ev'])} after +1c stress. It remains negative in train ({cents(btc['train']['btc_favorite']['ev'])}) and tune ({cents(btc['tune']['btc_favorite']['ev'])}), so this is a current-regime candidate, not a proven all-regime edge.",
            "",
            "The adaptive selector is rejected unless it is positive in tune, validation, and recent. Maker rows are execution bounds only; promotion requires a forward quote/order ledger because candle lows do not establish queue fills.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    result = run()
    (OUT / "late_forced_results.json").write_text(json.dumps(result, indent=2, sort_keys=True))
    (HERE / "LATE_FORCED_REPORT.md").write_text(render(result))
    print(render(result))


if __name__ == "__main__":
    main()
