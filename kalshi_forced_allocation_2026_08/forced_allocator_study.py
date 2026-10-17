#!/usr/bin/env python3
"""Causal forced-allocation replay for aligned BTC/ETH 15-minute markets."""

from __future__ import annotations

import csv
import json
import math
import random
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Any, Callable, Sequence

import numpy as np


HERE = Path(__file__).resolve().parent
RESEARCH = HERE.parent / "kalshi_band_stop_2026_08"
OLD_DATA = HERE.parent / "kalshi_crypto15m_2026_07" / "data"
RECENT_DATA = RESEARCH / "data"
OUT = HERE / "out"
SERIES = ("KXBTC15M", "KXETH15M")
TIMINGS = (8, 6, 4, 2, 1)
RIDGES = (0.1, 1.0, 10.0, 100.0)
EPSILON = 1e-9

import sys

sys.path.insert(0, str(RESEARCH))
from study_band_stop import load_markets, side_quotes  # noqa: E402


@dataclass
class AssetState:
    family: str
    ticker: str
    close_ts: int
    day: str
    result_yes: int
    rem: int
    yes_bid: float
    yes_ask: float
    next_yes_bid: float
    next_yes_ask: float
    mid: float
    other_ready: bool
    mom1: float
    mom3: float
    spread: float
    flips: int
    held: int
    path_vol: float


@dataclass
class PairState:
    close_ts: int
    day: str
    btc: AssetState | None
    eth: AssetState | None


@dataclass
class Choice:
    close_ts: int
    day: str
    policy: str
    family: str
    side: str
    signal_price: float
    fill_price: float
    predicted_probability: float
    predicted_ev: float
    won: bool
    pnl: float


@dataclass
class OffsetModel:
    center: np.ndarray
    scale: np.ndarray
    beta: np.ndarray

    def predict(self, features: np.ndarray, offset: np.ndarray) -> np.ndarray:
        transformed = np.clip((features - self.center) / self.scale, -8.0, 8.0)
        design = np.column_stack([np.ones(len(transformed)), transformed])
        # NumPy 2.0/Accelerate emits spurious FP warnings for finite ARM matmuls.
        with np.errstate(over="ignore", invalid="ignore", divide="ignore"):
            logits = np.clip(offset + design @ self.beta, -12.0, 12.0)
        logits = np.nan_to_num(logits, nan=0.0, posinf=12.0, neginf=-12.0)
        return np.clip(1.0 / (1.0 + np.exp(-logits)), 0.01, 0.99)


def fee(price: float) -> float:
    return 0.07 * price * (1.0 - price)


def logit(probability: float) -> float:
    probability = min(0.995, max(0.005, probability))
    return math.log(probability / (1.0 - probability))


def quote_map(market) -> dict[int, tuple[int, dict[str, float]]]:
    mapped = {}
    for index, candle in enumerate(market.candles):
        rem = round((market.close_ts - int(candle["t"])) / 60)
        quote = side_quotes(candle, "yes")
        if quote is not None:
            mapped[rem] = (index, quote)
    return mapped


def path_features(market, rem: int) -> AssetState | None:
    mapped = quote_map(market)
    if rem not in mapped:
        return None
    index, quote = mapped[rem]
    if index + 1 >= len(market.candles):
        return None
    next_quote = side_quotes(market.candles[index + 1], "yes")
    if next_quote is None:
        return None
    mid = (quote["bid_close"] + quote["ask_close"]) / 2.0

    def prior_mid(prior_rem: int) -> float:
        item = mapped.get(prior_rem)
        if item is None:
            return mid
        prior = item[1]
        return (prior["bid_close"] + prior["ask_close"]) / 2.0

    chronological = []
    for prior_rem in sorted((key for key in mapped if rem <= key <= 14), reverse=True):
        prior = mapped[prior_rem][1]
        chronological.append((prior["bid_close"] + prior["ask_close"]) / 2.0)
    leaders = [value >= 0.5 for value in chronological]
    flips = sum(left != right for left, right in zip(leaders, leaders[1:]))
    held = 0
    if leaders:
        final = leaders[-1]
        for leader in reversed(leaders):
            if leader != final:
                break
            held += 1
    deltas = np.diff(chronological[-6:]) if len(chronological) >= 2 else np.array([0.0])
    return AssetState(
        family=market.family,
        ticker=market.ticker,
        close_ts=market.close_ts,
        day=market.day,
        result_yes=1 if market.result == "yes" else 0,
        rem=rem,
        yes_bid=quote["bid_close"],
        yes_ask=quote["ask_close"],
        next_yes_bid=next_quote["bid_open"],
        next_yes_ask=next_quote["ask_open"],
        mid=mid,
        other_ready=False,
        mom1=mid - prior_mid(rem + 1),
        mom3=mid - prior_mid(rem + 3),
        spread=quote["ask_close"] - quote["bid_close"],
        flips=flips,
        held=held,
        path_vol=float(np.std(deltas)) if len(deltas) else 0.0,
    )


def load_pairs(data_dir: Path, rem: int) -> list[PairState]:
    by_family = {
        family: {market.close_ts: market for market in load_markets(data_dir, family)}
        for family in SERIES
    }
    close_times = sorted(set(by_family[SERIES[0]]) | set(by_family[SERIES[1]]))
    pairs = []
    for close_ts in close_times:
        states = {}
        for family in SERIES:
            market = by_family[family].get(close_ts)
            states[family] = path_features(market, rem) if market else None
        btc, eth = states[SERIES[0]], states[SERIES[1]]
        if btc:
            btc.other_ready = eth is not None
        if eth:
            eth.other_ready = btc is not None
        if btc or eth:
            day = (btc or eth).day
            pairs.append(PairState(close_ts, day, btc, eth))
    return pairs


def feature_vector(own: AssetState, other: AssetState | None) -> np.ndarray:
    other_mid = other.mid if other else 0.5
    own_favorite = own.mid >= 0.5
    other_favorite = other_mid >= 0.5
    return np.array(
        [
            logit(own.mid),
            logit(other_mid) - logit(own.mid),
            own.mom1,
            own.mom3,
            other.mom1 if other else 0.0,
            other.mom3 if other else 0.0,
            own.spread,
            other.spread if other else 0.02,
            float(own.flips),
            float(other.flips if other else 0),
            float(own.held),
            float(other.held if other else 0),
            own.path_vol,
            other.path_vol if other else 0.0,
            1.0 if own_favorite == other_favorite else 0.0,
        ],
        dtype=float,
    )


def model_rows(pairs: Sequence[PairState], family: str):
    features, offsets, outcomes = [], [], []
    for pair in pairs:
        own = pair.btc if family == SERIES[0] else pair.eth
        other = pair.eth if family == SERIES[0] else pair.btc
        if own is None:
            continue
        features.append(feature_vector(own, other))
        offsets.append(logit(own.mid))
        outcomes.append(own.result_yes)
    return np.asarray(features), np.asarray(offsets), np.asarray(outcomes)


def fit_offset_model(features: np.ndarray, offset: np.ndarray, outcomes: np.ndarray, ridge: float) -> OffsetModel:
    center = features.mean(axis=0)
    scale = features.std(axis=0)
    scale[scale < 1e-6] = 1.0
    transformed = np.clip((features - center) / scale, -8.0, 8.0)
    design = np.column_stack([np.ones(len(transformed)), transformed])
    beta = np.zeros(design.shape[1])
    penalty = np.eye(design.shape[1]) * ridge
    penalty[0, 0] = ridge * 0.05
    def objective(coefficients: np.ndarray) -> float:
        with np.errstate(over="ignore", invalid="ignore", divide="ignore"):
            logits = np.clip(offset + design @ coefficients, -30.0, 30.0)
        likelihood = np.sum(outcomes * logits - np.logaddexp(0.0, logits))
        return float(likelihood - 0.5 * coefficients @ penalty @ coefficients)

    current_objective = objective(beta)
    for _ in range(80):
        with np.errstate(over="ignore", invalid="ignore", divide="ignore"):
            logits = np.clip(offset + design @ beta, -12.0, 12.0)
        probabilities = 1.0 / (1.0 + np.exp(-logits))
        weights = np.maximum(probabilities * (1.0 - probabilities), 1e-5)
        with np.errstate(over="ignore", invalid="ignore", divide="ignore"):
            gradient = design.T @ (outcomes - probabilities) - penalty @ beta
            hessian = (design.T * weights) @ design + penalty
        try:
            step = np.linalg.solve(hessian, gradient)
        except np.linalg.LinAlgError:
            step = np.linalg.lstsq(hessian, gradient, rcond=None)[0]
        accepted = False
        step_scale = 1.0
        for _ in range(24):
            proposed = np.clip(beta + step_scale * step, -12.0, 12.0)
            proposed_objective = objective(proposed)
            if math.isfinite(proposed_objective) and proposed_objective >= current_objective - 1e-10:
                beta = proposed
                current_objective = proposed_objective
                accepted = True
                break
            step_scale *= 0.5
        if not accepted or np.max(np.abs(step_scale * step)) < 1e-7:
            break
    return OffsetModel(center, scale, beta)


def train_models(pairs: Sequence[PairState], ridge: float) -> dict[str, OffsetModel]:
    models = {}
    for family in SERIES:
        features, offsets, outcomes = model_rows(pairs, family)
        models[family] = fit_offset_model(features, offsets, outcomes, ridge)
    return models


def predict_pair(pair: PairState, models: dict[str, OffsetModel]) -> dict[str, float]:
    predictions = {}
    for family, own, other in (
        (SERIES[0], pair.btc, pair.eth),
        (SERIES[1], pair.eth, pair.btc),
    ):
        if own is None:
            continue
        features = feature_vector(own, other).reshape(1, -1)
        predictions[family] = float(
            models[family].predict(features, np.array([logit(own.mid)]))[0]
        )
    return predictions


def candidate(own: AssetState, side: str, probability_yes: float, extra_slippage: float):
    if side == "yes":
        signal = own.yes_ask
        next_open = own.next_yes_ask
        probability = probability_yes
        won = own.result_yes == 1
    else:
        signal = 1.0 - own.yes_bid
        next_open = 1.0 - own.next_yes_bid
        probability = 1.0 - probability_yes
        won = own.result_yes == 0
    fill = min(0.999, max(signal, next_open) + extra_slippage)
    predicted_ev = probability - fill - fee(fill)
    pnl = (1.0 if won else 0.0) - fill - fee(fill)
    return signal, fill, probability, predicted_ev, won, pnl


def choose_single(
    pair: PairState,
    probabilities: dict[str, float],
    policy: str,
    extra_slippage: float,
) -> Choice | None:
    actions = []
    for family, own in ((SERIES[0], pair.btc), (SERIES[1], pair.eth)):
        if own is None:
            continue
        p_yes = probabilities.get(family, own.mid)
        for side in ("yes", "no"):
            values = candidate(own, side, p_yes, extra_slippage)
            actions.append((family, side, own, values))
    if not actions:
        return None

    if policy == "btc_favorite":
        filtered = [action for action in actions if action[0] == SERIES[0]] or actions
        chosen = max(filtered, key=lambda action: probabilities.get(action[0], action[2].mid) if action[1] == "yes" else 1 - probabilities.get(action[0], action[2].mid))
    elif policy == "eth_favorite":
        filtered = [action for action in actions if action[0] == SERIES[1]] or actions
        chosen = max(filtered, key=lambda action: probabilities.get(action[0], action[2].mid) if action[1] == "yes" else 1 - probabilities.get(action[0], action[2].mid))
    elif policy == "strongest_favorite":
        chosen = max(actions, key=lambda action: max(action[2].mid, 1 - action[2].mid) if action[1] == ("yes" if action[2].mid >= 0.5 else "no") else -1)
    elif policy == "cheapest_favorite":
        favorites = [action for action in actions if action[1] == ("yes" if action[2].mid >= 0.5 else "no")]
        chosen = min(favorites, key=lambda action: action[3][0])
    else:
        chosen = max(actions, key=lambda action: (action[3][3], -SERIES.index(action[0]), action[1] == "yes"))

    family, side, own, values = chosen
    signal, fill, probability, predicted_ev, won, pnl = values
    return Choice(pair.close_ts, pair.day, policy, family, side, signal, fill, probability, predicted_ev, won, pnl)


def replay_policy(
    pairs: Sequence[PairState],
    models: dict[str, OffsetModel] | None,
    policy: str,
    extra_slippage: float,
) -> list[Choice]:
    choices = []
    for pair in pairs:
        model_probabilities = predict_pair(pair, models) if models else {}
        if policy in {"btc_favorite", "eth_favorite", "strongest_favorite", "cheapest_favorite", "market_prior"}:
            probabilities = {
                family: own.mid
                for family, own in ((SERIES[0], pair.btc), (SERIES[1], pair.eth))
                if own
            }
        else:
            probabilities = model_probabilities
        choice = choose_single(pair, probabilities, policy, extra_slippage)
        if choice:
            choices.append(choice)
    return choices


def replay_combo(
    pairs: Sequence[PairState], models: dict[str, OffsetModel], extra_slippage: float
) -> list[Choice]:
    combined = []
    for pair in pairs:
        probabilities = predict_pair(pair, models)
        legs = []
        for family, own in ((SERIES[0], pair.btc), (SERIES[1], pair.eth)):
            if own is None:
                continue
            p_yes = probabilities[family]
            leg_candidates = [
                (side, candidate(own, side, p_yes, extra_slippage))
                for side in ("yes", "no")
            ]
            legs.append((family, own, max(leg_candidates, key=lambda item: item[1][3])))
        if not legs:
            continue
        weight = 1.0 / len(legs)
        pnl = sum(weight * leg[2][1][5] for leg in legs)
        predicted_ev = sum(weight * leg[2][1][3] for leg in legs)
        fill = sum(weight * leg[2][1][1] for leg in legs)
        probability = sum(weight * leg[2][1][2] for leg in legs)
        won = pnl > 0
        combined.append(
            Choice(pair.close_ts, pair.day, "equal_capital_combo", "BTC+ETH", "+".join(leg[2][0] for leg in legs), fill, fill, probability, predicted_ev, won, pnl)
        )
    return combined


def maximum_drawdown(choices: Sequence[Choice]) -> float:
    equity = peak = drawdown = 0.0
    for choice in sorted(choices, key=lambda item: item.close_ts):
        equity += choice.pnl
        peak = max(peak, equity)
        drawdown = min(drawdown, equity - peak)
    return drawdown


def bootstrap(choices: Sequence[Choice], iterations: int = 5000) -> dict[str, float]:
    by_day = defaultdict(list)
    for choice in choices:
        by_day[choice.day].append(choice.pnl)
    days = sorted(by_day)
    if not days:
        return {"p_positive": 0.0, "p05": 0.0}
    rng = random.Random(411)
    samples = []
    for _ in range(iterations):
        values = []
        for _ in days:
            values.extend(by_day[rng.choice(days)])
        samples.append(mean(values))
    samples.sort()
    return {
        "p_positive": sum(value > 0 for value in samples) / len(samples),
        "p05": samples[int(0.05 * (len(samples) - 1))],
    }


def summarize(
    choices: Sequence[Choice], possible_windows: int, *, do_bootstrap: bool = True
) -> dict[str, Any]:
    if not choices:
        return {"n": 0, "coverage": 0.0, "ev": 0.0}
    days = len({choice.day for choice in choices})
    block = bootstrap(choices) if do_bootstrap else {"p_positive": 0.0, "p05": 0.0}
    family_mix = defaultdict(int)
    side_mix = defaultdict(int)
    for choice in choices:
        family_mix[choice.family] += 1
        side_mix[choice.side] += 1
    return {
        "n": len(choices),
        "days": days,
        "coverage": len(choices) / possible_windows,
        "trades_per_day": len(choices) / days,
        "win_rate": mean(choice.won for choice in choices),
        "mean_entry": mean(choice.fill_price for choice in choices),
        "ev": mean(choice.pnl for choice in choices),
        "pnl_per_day": sum(choice.pnl for choice in choices) / days,
        "turnover_per_day": sum(choice.fill_price for choice in choices) / days,
        "max_drawdown": maximum_drawdown(choices),
        "p_positive": block["p_positive"],
        "p05": block["p05"],
        "mean_predicted_ev": mean(choice.predicted_ev for choice in choices),
        "family_mix": dict(family_mix),
        "side_mix": dict(side_mix),
    }


def split_days(pairs: Sequence[PairState]):
    days = sorted({pair.day for pair in pairs})
    train_end = int(len(days) * 0.50)
    tune_end = int(len(days) * 0.70)
    train_days = set(days[:train_end])
    tune_days = set(days[train_end:tune_end])
    validation_days = set(days[tune_end:])
    return (
        [pair for pair in pairs if pair.day in train_days],
        [pair for pair in pairs if pair.day in tune_days],
        [pair for pair in pairs if pair.day in validation_days],
    )


def tuning_surface() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    rows = []
    for rem in TIMINGS:
        pairs = load_pairs(OLD_DATA, rem)
        train, tune, _ = split_days(pairs)
        for ridge in RIDGES:
            models = train_models(train, ridge)
            raw = replay_policy(tune, models, "residual_model", 0.0)
            stressed = replay_policy(tune, models, "residual_model", 0.01)
            raw_stats = summarize(raw, len(tune), do_bootstrap=False)
            stressed_stats = summarize(stressed, len(tune), do_bootstrap=False)
            rows.append(
                {
                    "rem": rem,
                    "ridge": ridge,
                    "raw_ev": raw_stats["ev"],
                    "stressed_ev": stressed_stats["ev"],
                    "stressed_p05": stressed_stats.get("p05", 0.0),
                    "coverage": stressed_stats["coverage"],
                    "n": stressed_stats["n"],
                }
            )
            print(f"tune rem={rem} ridge={ridge}: {100*stressed_stats['ev']:+.3f}c", flush=True)
    best = max(rows, key=lambda row: (row["stressed_ev"], row["stressed_p05"], row["coverage"]))
    return best, rows


def evaluate_spec(rem: int, ridge: float) -> dict[str, Any]:
    old_pairs = load_pairs(OLD_DATA, rem)
    recent_pairs = load_pairs(RECENT_DATA, rem)
    train, tune, validation = split_days(old_pairs)
    training = train + tune
    models_validation = train_models(training, ridge)
    models_recent = train_models(old_pairs, ridge)
    policies = (
        "btc_favorite",
        "eth_favorite",
        "strongest_favorite",
        "cheapest_favorite",
        "market_prior",
        "residual_model",
    )
    result = {
        "rem": rem,
        "ridge": ridge,
        "counts": {"old": len(old_pairs), "validation": len(validation), "recent": len(recent_pairs)},
        "validation": {},
        "recent": {},
    }
    for name, pairs, models in (
        ("validation", validation, models_validation),
        ("recent", recent_pairs, models_recent),
    ):
        for policy in policies:
            result[name][policy] = {}
            for stress in (0.0, 0.01):
                choices = replay_policy(pairs, models, policy, stress)
                result[name][policy][f"slip_{stress:.2f}"] = summarize(choices, len(pairs))
                if name == "recent" and policy == "residual_model" and stress == 0.0:
                    write_choices(OUT / "recent_residual_choices.csv", choices)
        result[name]["equal_capital_combo"] = {}
        for stress in (0.0, 0.01):
            choices = replay_combo(pairs, models, stress)
            result[name]["equal_capital_combo"][f"slip_{stress:.2f}"] = summarize(choices, len(pairs))
    return result


def write_choices(path: Path, choices: Sequence[Choice]) -> None:
    if not choices:
        return
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(asdict(choices[0]).keys()))
        writer.writeheader()
        writer.writerows(asdict(choice) for choice in choices)


def cents(value: float) -> str:
    return f"{100 * value:+.3f}c"


def report(best: dict[str, Any], tuning: Sequence[dict[str, Any]], result: dict[str, Any]) -> str:
    lines = [
        "# BTC/ETH Forced-Allocation Research",
        "",
        f"Generated: {datetime.now(timezone.utc).isoformat()}",
        "",
        "## Selected Specification",
        "",
        f"The discovery/tune process selected **{best['rem']} minutes remaining** and ridge "
        f"penalty **{best['ridge']}**. Every executable aligned window receives one selection.",
        "",
        "## Out-of-Sample Results",
        "",
    ]
    for split in ("validation", "recent"):
        lines.extend([f"### {split.title()}", "", "| Policy | EV/contract | +1c EV | PnL/day | +1c PnL/day | 5th pct (+1c) | Coverage | Max DD (+1c) |", "|---|---:|---:|---:|---:|---:|---:|---:|"])
        for policy, values in result[split].items():
            raw = values["slip_0.00"]
            stressed = values["slip_0.01"]
            lines.append(
                f"| {policy} | {cents(raw['ev'])} | {cents(stressed['ev'])} | "
                f"${raw['pnl_per_day']:+.2f} | ${stressed['pnl_per_day']:+.2f} | "
                f"{cents(stressed.get('p05', 0.0))} | {stressed['coverage']:.2%} | "
                f"${stressed['max_drawdown']:.2f} |"
            )
        lines.append("")
    residual_validation = result["validation"]["residual_model"]["slip_0.01"]
    residual_recent = result["recent"]["residual_model"]["slip_0.01"]
    profitable = (
        residual_validation["ev"] > 0
        and residual_validation.get("p05", 0) > 0
        and residual_recent["ev"] > 0
        and residual_recent.get("p05", 0) > 0
    )
    lines.extend(
        [
            "## Verdict",
            "",
            (
                "The forced allocator clears the preregistered positive-edge gate."
                if profitable
                else "The forced allocator does **not** clear the positive-edge gate. Its ranking may still be the least-loss deterministic policy, but it is not proven profitable."
            ),
            "",
            "A two-leg equal-capital combo cannot have higher predicted arithmetic EV than selecting "
            "the better single leg: it averages the best BTC and ETH opportunities while the single "
            "allocator takes their maximum. Its purpose is lower variance, not additional edge.",
            "",
            "## Allocation Mix",
            "",
            f"Recent residual model: `{json.dumps(result['recent']['residual_model']['slip_0.00'].get('family_mix', {}), sort_keys=True)}`; "
            f"sides `{json.dumps(result['recent']['residual_model']['slip_0.00'].get('side_mix', {}), sort_keys=True)}`.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    best, tuning = tuning_surface()
    result = evaluate_spec(int(best["rem"]), float(best["ridge"]))
    (OUT / "tuning.json").write_text(json.dumps(tuning, indent=2, sort_keys=True) + "\n")
    (OUT / "results.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    (HERE / "REPORT.md").write_text(report(best, tuning, result) + "\n")
    print(f"wrote {HERE / 'REPORT.md'}", flush=True)


if __name__ == "__main__":
    main()
