#!/usr/bin/env python3
"""Replay an exact final-minute BRTI-average strategy against recorded books."""

from __future__ import annotations

import bisect
import json
import math
import random
import statistics
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

import study


ROOT = Path(__file__).resolve().parent
BRTI_CACHE = ROOT / "data" / "brti_200ms_hours.jsonl"
RECONSTRUCTION_TOLERANCE = 0.02
SIGMA_INFLATION = 1.25
MIN_MODEL_PROBABILITY = 0.97
MIN_MODEL_EV = 0.01
EXECUTION_STRESS = 0.0025
MIN_DEPTH = 10.0
MAX_SPREAD = 0.02


@dataclass(frozen=True)
class State:
    ticker: str
    close_ts: int
    result: str
    seconds_remaining: int
    p_yes: float
    market_mid: float
    yes_bid: float
    yes_ask: float
    bid_size: float
    ask_size: float
    sigma: float
    opening_error: float
    closing_error: float


@dataclass(frozen=True)
class Signal:
    state: State
    side: str
    entry: float
    p_win: float
    model_ev: float
    pnl: float


class BrtiTape:
    def __init__(self, timestamps_ms: list[int], values: list[float]):
        self.timestamps_ms = timestamps_ms
        self.values = values
        self.by_second = {
            timestamp // 1000: value
            for timestamp, value in zip(timestamps_ms, values)
            if timestamp % 1000 == 0
        }

    @classmethod
    def load(cls, path: Path = BRTI_CACHE) -> "BrtiTape":
        points: dict[int, float] = {}
        with path.open() as handle:
            for line in handle:
                row = json.loads(line)
                for timestamp, value in row["values"]:
                    points[int(timestamp)] = float(value)
        timestamps = sorted(points)
        return cls(timestamps, [points[timestamp] for timestamp in timestamps])

    def second_window(self, start_ts: int, end_ts: int) -> list[float] | None:
        values = [self.by_second.get(timestamp) for timestamp in range(start_ts, end_ts)]
        return None if any(value is None for value in values) else values

    def boundary_average(self, boundary_ts: int) -> float | None:
        values = self.second_window(boundary_ts - 60, boundary_ts)
        return None if values is None or len(values) != 60 else statistics.mean(values)

    def innovation_sigma(self, end_ts: int, lookback: int = 300) -> float | None:
        values = self.second_window(end_ts - lookback, end_ts)
        if values is None or len(values) < 30:
            return None
        changes = [right - left for left, right in zip(values, values[1:])]
        return statistics.stdev(changes) if len(changes) > 1 else None


def normal_cdf(value: float) -> float:
    return 0.5 * (1 + math.erf(value / math.sqrt(2)))


def integrated_probability(
    opening_average: float,
    observed_values: list[float],
    remaining_seconds: int,
    sigma: float,
    sigma_inflation: float = SIGMA_INFLATION,
) -> float:
    """Probability the complete 60-second average is >= opening_average."""
    if not observed_values or remaining_seconds < 0:
        raise ValueError("Observed values and non-negative remaining time required")
    if len(observed_values) + remaining_seconds != 60:
        raise ValueError("Observed and remaining seconds must total 60")
    if remaining_seconds == 0:
        return float(statistics.mean(observed_values) >= opening_average)
    current = observed_values[-1]
    expected_total = sum(observed_values) + remaining_seconds * current
    target_total = 60 * opening_average
    variance_weight = sum(index * index for index in range(1, remaining_seconds + 1))
    denominator = max(sigma * sigma_inflation * math.sqrt(variance_weight), 1e-12)
    return normal_cdf((expected_total - target_total) / denominator)


def first_final_minute_snapshot(rows: list[dict], close_ts: int) -> dict | None:
    eligible = [row for row in rows if 20 <= close_ts - row["ts"] <= 40]
    return min(eligible, key=lambda row: row["ts"], default=None)


def build_states(markets: list[study.Market], books: dict, tape: BrtiTape) -> tuple[list[State], dict]:
    states = []
    reconstruction = {"opening": [], "closing": [], "missing": 0, "rejected": 0}
    for market in markets:
        rows = books.get(market.ticker)
        snapshot = first_final_minute_snapshot(rows or [], market.close_ts)
        if snapshot is None or market.strike is None or market.expiration is None:
            continue

        opening = tape.boundary_average(market.close_ts - 15 * 60)
        closing = tape.boundary_average(market.close_ts)
        if opening is None or closing is None:
            reconstruction["missing"] += 1
            continue
        opening_error = opening - market.strike
        closing_error = closing - market.expiration
        reconstruction["opening"].append(opening_error)
        reconstruction["closing"].append(closing_error)
        if (
            abs(opening_error) > RECONSTRUCTION_TOLERANCE
            or abs(closing_error) > RECONSTRUCTION_TOLERANCE
        ):
            reconstruction["rejected"] += 1
            continue

        seconds_remaining = market.close_ts - snapshot["ts"]
        observed = tape.second_window(market.close_ts - 60, snapshot["ts"])
        sigma = tape.innovation_sigma(snapshot["ts"])
        if observed is None or sigma is None or len(observed) + seconds_remaining != 60:
            reconstruction["missing"] += 1
            continue
        p_yes = integrated_probability(market.strike, observed, seconds_remaining, sigma)
        states.append(
            State(
                ticker=market.ticker,
                close_ts=market.close_ts,
                result=market.result,
                seconds_remaining=seconds_remaining,
                p_yes=p_yes,
                market_mid=snapshot["mid"],
                yes_bid=snapshot["yes_bid"],
                yes_ask=snapshot["yes_ask"],
                bid_size=snapshot["bid_size"],
                ask_size=snapshot["ask_size"],
                sigma=sigma,
                opening_error=opening_error,
                closing_error=closing_error,
            )
        )
    return sorted(states, key=lambda state: state.close_ts), reconstruction


def select_signals(states: list[State]) -> list[Signal]:
    signals = []
    for state in states:
        if state.p_yes >= 0.5:
            side = "yes"
            p_win = state.p_yes
            entry = state.yes_ask
            depth = state.ask_size
        else:
            side = "no"
            p_win = 1 - state.p_yes
            entry = 1 - state.yes_bid
            depth = state.bid_size
        spread = state.yes_ask - state.yes_bid
        model_ev = p_win - entry - study.fee(entry) - EXECUTION_STRESS
        if (
            p_win < MIN_MODEL_PROBABILITY
            or model_ev < MIN_MODEL_EV
            or spread > MAX_SPREAD + study.EPSILON
            or depth < MIN_DEPTH
        ):
            continue
        won = state.result == side
        pnl = study.settlement_pnl(entry, won)
        signals.append(Signal(state, side, entry, p_win, model_ev, pnl))
    return signals


def brier(states: list[State], source: str) -> float:
    predictions = [state.p_yes if source == "model" else state.market_mid for state in states]
    outcomes = [float(state.result == "yes") for state in states]
    return statistics.mean((prediction - outcome) ** 2 for prediction, outcome in zip(predictions, outcomes))


def calibration_rows(states: list[State]) -> list[tuple[str, int, float, float]]:
    output = []
    for low in (0.50, 0.70, 0.80, 0.90, 0.95, 0.97, 0.99):
        high = 1.01
        selected = []
        for state in states:
            strength = max(state.p_yes, 1 - state.p_yes)
            if low <= strength < high:
                predicted_side = "yes" if state.p_yes >= 0.5 else "no"
                selected.append((strength, state.result == predicted_side))
        if selected:
            output.append(
                (
                    f">={low:.2f}",
                    len(selected),
                    statistics.mean(value for value, _ in selected),
                    statistics.mean(float(won) for _, won in selected),
                )
            )
    return output


def bootstrap_days(signals: list[Signal], iterations: int = 20_000) -> tuple[float, float, int]:
    grouped = defaultdict(list)
    for signal in signals:
        grouped[signal.state.close_ts // 86400].append(signal.pnl - EXECUTION_STRESS)
    days = sorted(grouped)
    if not days:
        return 0.0, float("nan"), 0
    generator = random.Random(20260807)
    means = []
    for _ in range(iterations):
        sample = [generator.choice(days) for _ in days]
        values = [value for day in sample for value in grouped[day]]
        means.append(100 * statistics.mean(values))
    means.sort()
    return (
        sum(value > 0 for value in means) / len(means),
        means[int(0.05 * (len(means) - 1))],
        len(days),
    )


def print_reconstruction(reconstruction: dict) -> None:
    for label in ("opening", "closing"):
        errors = reconstruction[label]
        print(
            f"{label:8} n={len(errors):3d} "
            f"median_abs={100*statistics.median(map(abs, errors)):.3f}c "
            f"mean_abs={100*statistics.mean(map(abs, errors)):.3f}c "
            f"max_abs={100*max(map(abs, errors)):.3f}c"
        )
    print(
        f"reconstruction rejected={reconstruction['rejected']} "
        f"missing={reconstruction['missing']} tolerance={100*RECONSTRUCTION_TOLERANCE:.1f}c"
    )


def print_segment(label: str, states: list[State]) -> None:
    signals = select_signals(states)
    print(f"\n{label}: states={len(states)} model_brier={brier(states, 'model'):.5f} market_brier={brier(states, 'market'):.5f}")
    if not signals:
        print("signals=0")
    else:
        pnl = [signal.pnl for signal in signals]
        wins = sum(value > 0 for value in pnl)
        probability, p05, days = bootstrap_days(signals)
        print(
            f"signals={len(signals)} wins={wins}/{len(signals)} ({wins/len(signals):.1%}) "
            f"raw={100*statistics.mean(pnl):+.3f}c "
            f"stress25={100*(statistics.mean(pnl)-EXECUTION_STRESS):+.3f}c "
            f"model_EV={100*statistics.mean(signal.model_ev for signal in signals):+.3f}c "
            f"days={days} bootP={probability:.1%} p05={p05:+.3f}c"
        )
        print(
            f"mean_entry={statistics.mean(signal.entry for signal in signals):.4f} "
            f"mean_seconds_left={statistics.mean(signal.state.seconds_remaining for signal in signals):.1f} "
            f"YES/NO={sum(signal.side == 'yes' for signal in signals)}/"
            f"{sum(signal.side == 'no' for signal in signals)}"
        )
    for name, count, predicted, realized in calibration_rows(states):
        print(f"calibration {name}: n={count:3d} predicted={predicted:.3%} realized={realized:.3%}")


def main() -> None:
    markets = study.load_markets()
    books = study.load_books()
    tape = BrtiTape.load()
    states, reconstruction = build_states(markets, books, tape)
    print(
        f"markets={len(markets)} book_tickers={len(books)} "
        f"whole_second_BRTI={len(tape.by_second)} states={len(states)}"
    )
    print_reconstruction(reconstruction)
    if len(states) < 2:
        return
    split = states[len(states) // 2].close_ts
    first = [state for state in states if state.close_ts < split]
    holdout = [state for state in states if state.close_ts >= split]
    print(f"split_utc={split}")
    print_segment("diagnostic first half", first)
    print_segment("locked final half", holdout)
    print_segment("all", states)


if __name__ == "__main__":
    main()
