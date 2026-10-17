"""Stage-B integer portfolio translations for the NEMA first-passage signal."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd


HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path[:0] = [str(HERE), str(ROOT / "trend_bot"),
                str(ROOT / "trend_bot" / "research"),
                str(ROOT / "strategy_extension_2026_07"),
                str(ROOT / "adaptive_rotation_2026_07")]

import rotation_grid as rg  # noqa: E402
import strategy  # noqa: E402
from run_adaptive_rotation import (  # noqa: E402
    PRODUCTION, START, SYMS, baseline_policy,
)
from run_stage_a import (  # noqa: E402
    candidate_directions, normalized_ema_score, pnl_stats,
    roll_clean_log_returns, simulate_direction,
)


def largest_remainder(values: np.ndarray, total: int = 3) -> np.ndarray:
    out = np.zeros(len(values), dtype=int)
    positive = np.clip(np.asarray(values, dtype=float), 0.0, None)
    if not np.isfinite(positive).all() or positive.sum() <= 0:
        return out
    raw = total * positive / positive.sum()
    out = np.floor(raw).astype(int)
    for index in np.argsort(-(raw - out))[:total - out.sum()]:
        out[index] += 1
    return out


def naive_strength_allocation(scores: pd.DataFrame) -> pd.DataFrame:
    rows = np.vstack([largest_remainder(row) for row in scores.to_numpy()])
    return pd.DataFrame(rows, index=scores.index, columns=scores.columns)


def weighted_correlation(returns: pd.DataFrame, halflife: int = 52):
    clean = returns.dropna()
    if len(clean) < 52:
        return np.eye(len(returns.columns))
    clean = clean.tail(156)
    age = np.arange(len(clean) - 1, -1, -1)
    weights = np.exp(np.log(0.5) * age / halflife)
    weights /= weights.sum()
    values = clean.to_numpy(dtype=float)
    mean = np.sum(values * weights[:, None], axis=0)
    centered = values - mean
    covariance = centered.T @ (centered * weights[:, None])
    scale = np.sqrt(np.clip(np.diag(covariance), 1e-12, None))
    correlation = covariance / np.outer(scale, scale)
    return np.clip((correlation + correlation.T) / 2.0, -1.0, 1.0)


def inverse_sqrt(matrix: np.ndarray, eigen_floor: float = 0.10):
    eigenvalues, eigenvectors = np.linalg.eigh(matrix)
    eigenvalues = np.maximum(eigenvalues, eigen_floor)
    return eigenvectors @ np.diag(eigenvalues ** -0.5) @ eigenvectors.T


def arp_strength_allocation(scores: pd.DataFrame,
                            bars: dict[str, pd.DataFrame]) -> pd.DataFrame:
    weekly_returns = pd.concat({
        symbol: roll_clean_log_returns(bars[symbol]).resample("W-FRI").sum()
        for symbol in SYMS
    }, axis=1)
    result = pd.DataFrame(0, index=scores.index, columns=SYMS, dtype=int)
    week_keys = (scores.index.normalize()
                 - pd.to_timedelta(scores.index.dayofweek, unit="D"))
    for monday in week_keys.unique().sort_values():
        history = weekly_returns[weekly_returns.index < monday]
        transform = inverse_sqrt(weighted_correlation(history))
        mask = week_keys == monday
        block = scores.loc[mask].to_numpy(dtype=float)
        transformed = block @ transform.T
        quantities = np.vstack([largest_remainder(row) for row in transformed])
        result.loc[mask] = quantities
    return result


def quantity_lookup(policy: pd.DataFrame, symbol: str):
    values = policy[symbol]

    def lookup(stamp):
        return int(values.asof(stamp)) if stamp >= values.index[0] else 0
    return lookup


def allocate_capacity(requested: np.ndarray, available: int,
                      scores: np.ndarray | None = None) -> np.ndarray:
    """Exact integer grants; optional scores prioritize scarce capacity."""
    requested = np.asarray(requested, dtype=int)
    granted = np.zeros(len(requested), dtype=int)
    if available <= 0 or requested.sum() <= 0:
        return granted
    if requested.sum() <= available:
        return requested.copy()
    if scores is not None and np.isfinite(scores).all():
        # Stable sorting makes the supplied pending order the deterministic
        # tie-break, while maximizing a linear per-contract objective.
        for index in np.argsort(-np.asarray(scores, dtype=float), kind="stable"):
            quantity = min(int(requested[index]), available - granted.sum())
            granted[index] = quantity
            if granted.sum() == available:
                break
        return granted
    raw = available * requested / requested.sum()
    granted = np.floor(raw).astype(int)
    for index in np.argsort(-(raw - granted))[:available - granted.sum()]:
        granted[index] += 1
    return granted


def simulate_capped(directions: dict[str, pd.Series], policy: pd.DataFrame,
                    bars: dict[str, pd.DataFrame], cap: int,
                    config: dict = PRODUCTION,
                    priority: dict[str, pd.Series] | None = None) -> pd.DataFrame:
    """Synchronized long-only K2 FSM with a hard concurrent quantity cap."""
    frames = {symbol: strategy.signal_frame(bars[symbol], config)
              for symbol in SYMS}
    states = {symbol: {"pos": 0, "pending": 0, "pending_score": np.nan,
                       "entry": np.nan,
                       "high": np.nan, "stop": np.nan, "entry_time": None}
              for symbol in SYMS}
    rows = []
    times = bars[SYMS[0]].index
    for symbol in SYMS[1:]:
        times = times.union(bars[symbol].index)
    locations = {symbol: {stamp: i for i, stamp in enumerate(bars[symbol].index)}
                 for symbol in SYMS}

    for stamp in times:
        for symbol in SYMS:
            if (states[symbol]["pending"] and stamp in locations[symbol]
                    and int(bars[symbol]["roll"].iloc[locations[symbol][stamp]])):
                states[symbol]["pending"] = 0
                states[symbol]["pending_score"] = np.nan

        pending = [(symbol, states[symbol]["pending"]) for symbol in SYMS
                   if states[symbol]["pending"] and stamp in locations[symbol]]
        existing = sum(states[symbol]["pos"] for symbol in SYMS)
        available = max(cap - existing, 0)
        fills = {symbol: 0 for symbol in SYMS}
        if pending and available:
            requested = np.array([quantity for _, quantity in pending], dtype=int)
            scores = (np.array([states[symbol]["pending_score"]
                                for symbol, _ in pending], dtype=float)
                      if priority is not None else None)
            granted = allocate_capacity(requested, available, scores)
            fills.update({symbol: int(quantity)
                          for (symbol, _), quantity in zip(pending, granted)})
        for symbol, _ in pending:
            state = states[symbol]
            i = locations[symbol][stamp]
            state["pending"] = 0
            state["pending_score"] = np.nan
            quantity = fills[symbol]
            if quantity:
                state["pos"] = quantity
                state["entry"] = float(bars[symbol]["open"].iloc[i])
                state["high"] = state["entry"]
                state["stop"] = (state["entry"] - config["k_trail"]
                                 * float(frames[symbol]["atr"].iloc[i - 1]))
                state["entry_time"] = stamp
                if not np.isfinite(state["stop"]):
                    state["pos"] = 0

        for symbol in SYMS:
            if stamp not in locations[symbol]:
                continue
            i = locations[symbol][stamp]
            bar = bars[symbol].iloc[i]
            signal = frames[symbol].iloc[i]
            state = states[symbol]
            if state["pos"] and int(bar["roll"]):
                exit_px = (float(bars[symbol]["close"].iloc[i - 1]) if i
                           else float(bar["open"]))
                rows.append((state["entry_time"], stamp, symbol,
                             state["entry"], exit_px,
                             exit_px - state["entry"], "roll", 1,
                             state["pos"]))
                state["pos"] = 0
            if state["pos"]:
                if float(bar["low"]) <= state["stop"]:
                    exit_px = min(state["stop"], float(bar["open"]))
                    rows.append((state["entry_time"], stamp, symbol,
                                 state["entry"], exit_px,
                                 exit_px - state["entry"], "stop", 1,
                                 state["pos"]))
                    state["pos"] = 0
                else:
                    state["high"] = max(state["high"], float(bar["high"]))
                    state["stop"] = max(
                        state["stop"], state["high"]
                        - config["k_trail"] * float(signal["atr"]))
            if not state["pos"] and not state["pending"]:
                direction = int(directions[symbol].asof(stamp))
                # Sparse first-passage events must match this exact bar; asof
                # is only used for aligned lookup and cannot carry a pulse.
                if stamp not in directions[symbol].index:
                    direction = 0
                elif int(directions[symbol].loc[stamp]) != 1:
                    direction = 0
                if direction:
                    requested = int(policy[symbol].asof(stamp))
                    if requested > 0:
                        state["pending"] = requested
                        state["pending_score"] = (
                            float(priority[symbol].asof(stamp))
                            if priority is not None else np.nan)

    trades = pd.DataFrame(rows, columns=[
        "entry_time", "exit_time", "sym", "entry_px", "exit_px", "points",
        "reason", "dir", "qty",
    ])
    return trades[trades["entry_time"] >= START].sort_values(
        "exit_time").reset_index(drop=True)


def attach_initial_risk(trades: pd.DataFrame,
                        bars: dict[str, pd.DataFrame], config=PRODUCTION):
    out = trades.copy()
    atrs = {symbol: strategy.signal_frame(bars[symbol], config)["atr"]
            for symbol in out["sym"].unique()}
    risks = []
    for trade in out.itertuples():
        i = bars[trade.sym].index.get_loc(trade.entry_time)
        atr = float(atrs[trade.sym].iloc[i - 1])
        risks.append(config["k_trail"] * atr * rg.MULT[trade.sym] * trade.qty)
    out["initial_risk"] = risks
    return out


def risk_stats(trades: pd.DataFrame):
    events = []
    for trade in trades.itertuples():
        events.extend(((trade.entry_time, 1, trade.initial_risk),
                       (trade.exit_time, 0, -trade.initial_risk)))
    if not events:
        return {"initial_risk_sum": 0.0, "max_initial_risk": 0.0}
    frame = pd.DataFrame(events, columns=("time", "order", "change"))
    frame = frame.sort_values(["time", "order"])
    return {"initial_risk_sum": float(trades["initial_risk"].sum()),
            "max_initial_risk": float(frame["change"].cumsum().max())}


def run():
    rg.START, rg.SCFG = START, dict(PRODUCTION)
    bars = {symbol: rg.load_60m(symbol) for symbol in SYMS}
    daily = {symbol: bars[symbol]["close"].resample("1D").last().dropna()
             for symbol in SYMS}
    index = bars[SYMS[0]].index
    for symbol in SYMS[1:]:
        index = index.intersection(bars[symbol].index)
    scores = pd.concat({symbol: normalized_ema_score(bars[symbol]).reindex(index)
                        for symbol in SYMS}, axis=1)
    weekly = baseline_policy(daily, index)
    policies = {
        "weekly": weekly,
        "fixed3": pd.DataFrame(0, index=index, columns=SYMS, dtype=int),
        "naive_strength": naive_strength_allocation(scores),
        "arp_strength": arp_strength_allocation(scores, bars),
    }
    policies["fixed3"].loc[:, :] = 3
    all_directions = {symbol: candidate_directions(bars[symbol])
                      for symbol in SYMS}
    direction_sets = {
        "donchian_weekly": {
            symbol: all_directions[symbol]["donchian20"] for symbol in SYMS},
        "nema_weekly": {
            symbol: all_directions[symbol]["nema_cross"] for symbol in SYMS},
        "nema_fixed3": {
            symbol: all_directions[symbol]["nema_cross"] for symbol in SYMS},
        "nema_naive_strength": {
            symbol: all_directions[symbol]["nema_cross"] for symbol in SYMS},
        "nema_arp_strength": {
            symbol: all_directions[symbol]["nema_cross"] for symbol in SYMS},
    }
    policy_for = {
        "donchian_weekly": policies["weekly"],
        "nema_weekly": policies["weekly"],
        "nema_fixed3": policies["fixed3"],
        "nema_naive_strength": policies["naive_strength"],
        "nema_arp_strength": policies["arp_strength"],
    }

    rows, daily_pnl = [], {}
    for name, directions in direction_sets.items():
        policy = policy_for[name]
        pieces = [simulate_direction(
            bars[symbol], symbol, directions[symbol],
            quantity=quantity_lookup(policy, symbol)) for symbol in SYMS]
        trades = pd.concat(pieces).sort_values("exit_time").reset_index(drop=True)
        trades = attach_initial_risk(trades, bars)
        pnl, stats = pnl_stats(trades, bars)
        rows.append({"candidate": name, "mode": "entry_bound", **stats,
                     **risk_stats(trades)})
        daily_pnl[f"{name}_entry_bound"] = pnl.groupby(
            pnl.index.normalize()).sum()
        print(name, "entry_bound", stats, flush=True)

        for cap in (3, 6):
            capped = simulate_capped(directions, policy, bars, cap)
            capped = attach_initial_risk(capped, bars)
            capped_pnl, capped_stats = pnl_stats(capped, bars)
            rows.append({"candidate": name, "mode": f"cap{cap}",
                         **capped_stats, **risk_stats(capped)})
            daily_pnl[f"{name}_cap{cap}"] = capped_pnl.groupby(
                capped_pnl.index.normalize()).sum()
            print(name, f"cap{cap}", capped_stats, flush=True)

    result = pd.DataFrame(rows)
    result.to_csv(HERE / "stage_b_results.csv", index=False)
    pd.concat(daily_pnl, axis=1).fillna(0.0).to_parquet(
        HERE / "stage_b_daily_pnl.parquet")
    print(result.to_string(index=False,
          float_format=lambda value: f"{value:,.3f}"))
    return result


if __name__ == "__main__":
    run()
