"""Deep validation of the pre-registered adaptive-rotation survivors."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd


HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path[:0] = [str(HERE), str(ROOT / "trend_bot"),
                str(ROOT / "trend_bot" / "research"),
                str(ROOT / "strategy_extension_2026_07")]

import rotation_grid as rg  # noqa: E402
import strategy  # noqa: E402
from run_adaptive_rotation import (D15, PRODUCTION, START, SYMS, allocation,
                                   baseline_policy, feature_library,
                                   maximum_exposure, policy_quantity)  # noqa: E402
from run_policy_search import hourly_mtm  # noqa: E402


def simulate_independent(policy, bars):
    return pd.concat([
        rg.simulate_weighted(bars[sym], sym,
                             policy_quantity(policy, sym)).assign(dir=1)
        for sym in SYMS
    ]).sort_values("exit_time").reset_index(drop=True)


def simulate_with_global_cap(policy: pd.DataFrame,
                             bars: dict[str, pd.DataFrame], cap: int):
    """Synchronized corrected FSM with a strict concurrent quantity cap."""
    frames = {s: strategy.signal_frame(bars[s], rg.SCFG) for s in SYMS}
    states = {s: {"pos": 0, "pending": 0, "entry": np.nan,
                  "high": np.nan, "stop": np.nan, "entry_time": None}
              for s in SYMS}
    rows = []
    all_times = bars[SYMS[0]].index
    for sym in SYMS[1:]:
        all_times = all_times.union(bars[sym].index)
    locations = {s: {stamp: i for i, stamp in enumerate(bars[s].index)}
                 for s in SYMS}

    for stamp in all_times:
        # The corrected reference cancels a pending entry when the next bar is
        # a contract-roll seam; never fill the new contract at an old-contract
        # signal/price relationship.
        for sym in SYMS:
            if (states[sym]["pending"] and stamp in locations[sym]
                    and int(bars[sym]["roll"].iloc[locations[sym][stamp]]) == 1):
                states[sym]["pending"] = 0
        # Pending market entries all execute at this bar's open.  Allocate any
        # scarce capacity proportionally by requested size, with deterministic
        # largest-remainder ties instead of symbol-order preference.
        pending = [(s, states[s]["pending"]) for s in SYMS
                   if states[s]["pending"] and stamp in locations[s]]
        existing = sum(states[s]["pos"] for s in SYMS)
        available = max(cap - existing, 0)
        fills = {s: 0 for s in SYMS}
        if pending and available:
            requested = np.array([q for _, q in pending], dtype=float)
            if requested.sum() <= available:
                granted = requested.astype(int)
            else:
                raw = available * requested / requested.sum()
                granted = np.floor(raw).astype(int)
                for j in np.argsort(-(raw - granted))[:available - granted.sum()]:
                    granted[j] += 1
            fills.update({s: int(q) for (s, _), q in zip(pending, granted)})
        for sym, _ in pending:
            state = states[sym]
            i = locations[sym][stamp]
            state["pending"] = 0
            quantity = fills[sym]
            if quantity:
                state["pos"] = quantity
                state["entry"] = float(bars[sym]["open"].iloc[i])
                state["high"] = state["entry"]
                state["stop"] = (state["entry"]
                                 - rg.SCFG["k_trail"]
                                 * float(frames[sym]["atr"].iloc[i - 1]))
                state["entry_time"] = stamp
                if not np.isfinite(state["stop"]):
                    state["pos"] = 0

        for sym in SYMS:
            if stamp not in locations[sym]:
                continue
            i = locations[sym][stamp]
            bar, signal = bars[sym].iloc[i], frames[sym].iloc[i]
            state = states[sym]
            if state["pos"] and int(bar["roll"]) == 1:
                exit_px = (float(bars[sym]["close"].iloc[i - 1]) if i
                           else float(bar["open"]))
                rows.append((state["entry_time"], stamp, sym,
                             state["entry"], exit_px,
                             exit_px - state["entry"], "roll",
                             state["pos"]))
                state["pos"] = 0
            if state["pos"]:
                if float(bar["low"]) <= state["stop"]:
                    exit_px = min(state["stop"], float(bar["open"]))
                    rows.append((state["entry_time"], stamp, sym,
                                 state["entry"], exit_px,
                                 exit_px - state["entry"], "stop",
                                 state["pos"]))
                    state["pos"] = 0
                else:
                    state["high"] = max(state["high"], float(bar["high"]))
                    state["stop"] = max(
                        state["stop"], state["high"]
                        - rg.SCFG["k_trail"] * float(signal["atr"]))
            if (not state["pos"] and not state["pending"]
                    and bool(signal["signal"])
                    and i > rg.SCFG["donchian_n"]):
                target = int(policy[sym].asof(stamp))
                if target > 0:
                    state["pending"] = target

    trades = pd.DataFrame(rows, columns=[
        "entry_time", "exit_time", "sym", "entry_px", "exit_px", "points",
        "reason", "qty",
    ])
    trades["dir"] = 1
    return trades[trades["entry_time"] >= START].sort_values(
        "exit_time").reset_index(drop=True)


def pnl_stats(trades, bars, friction=3.5):
    pnl = hourly_mtm(trades, bars, friction)
    equity = pnl.cumsum()
    drawdown = equity - equity.cummax().clip(lower=0.0)
    yearly = pnl.groupby(pnl.index.year).sum()
    return pnl, {
        "net": float(pnl.sum()), "maxDD": float(drawdown.min()),
        "worst": float(yearly.min()), "worst_year": int(yearly.idxmin()),
        "contracts": int(trades["qty"].sum()),
        "max_core": maximum_exposure(trades),
    }


def paired_bootstrap(candidate: pd.Series, baseline: pd.Series,
                     seed=20260721, samples=10_000, block=63):
    delta = candidate.sub(baseline, fill_value=0.0)
    daily = delta.groupby(delta.index.normalize()).sum()
    n = len(daily)
    values = daily.to_numpy()
    rng = np.random.default_rng(seed)
    starts = rng.integers(0, n - block + 1,
                          size=(samples, int(np.ceil(n / block))))
    selected = np.take(values, starts[:, :, None] + np.arange(block))
    totals = selected.reshape(samples, -1)[:, :n].sum(axis=1)
    return {"delta": float(values.sum()),
            "p_positive": float((totals > 0).mean()),
            "ci_low": float(np.percentile(totals, 2.5)),
            "ci_high": float(np.percentile(totals, 97.5))}


def run_config(config, label):
    rg.START, rg.SCFG = START, dict(config)
    bars = {s: rg.load_60m(s) for s in SYMS}
    daily, index, scores = feature_library(bars)
    policies = {
        "weekly": baseline_policy(daily, index),
        "fast5_w2_grad": allocation(scores["week35_R5_W2"], "graduated"),
        "fast5_w2_hard": allocation(scores["week35_R5_W2"], "hard"),
        "fast10_w1_grad": allocation(scores["week35_R10_W1"], "graduated"),
    }
    original = strategy.signal_frame
    cached = {id(frame): original(frame, rg.SCFG) for frame in bars.values()}
    strategy.signal_frame = lambda frame, ignored: cached[id(frame)]
    results, pnl = {}, {}
    try:
        for name, policy in policies.items():
            trades = simulate_independent(policy, bars)
            pnl[name], stats = pnl_stats(trades, bars)
            stats["mode"] = "entry_bound"
            results[f"{name}_entry_bound"] = stats
        # Assert the synchronized engine reproduces the corrected reference
        # before relying on its strict-cap branches.
        for name, policy in policies.items():
            unlimited = simulate_with_global_cap(policy, bars, cap=999)
            reference = simulate_independent(policy, bars)
            cols = ["entry_time", "exit_time", "sym", "entry_px", "exit_px",
                    "points", "reason", "qty"]
            unlimited_cmp = unlimited[cols].sort_values(
                ["sym", "entry_time", "exit_time"]).reset_index(drop=True)
            reference_cmp = reference[cols].sort_values(
                ["sym", "entry_time", "exit_time"]).reset_index(drop=True)
            if not unlimited_cmp.equals(reference_cmp):
                raise AssertionError(f"{label}/{name}: synchronized parity failed")
        for cap in (3, 6):
            for name, policy in policies.items():
                trades = simulate_with_global_cap(policy, bars, cap)
                capped_pnl, stats = pnl_stats(trades, bars)
                stats["mode"] = f"cap{cap}"
                results[f"{name}_cap{cap}"] = stats
                pnl[f"{name}_cap{cap}"] = capped_pnl
    finally:
        strategy.signal_frame = original

    for suffix in ("entry_bound", "cap3", "cap6"):
        baseline = pnl["weekly" if suffix == "entry_bound"
                       else f"weekly_{suffix}"]
        for candidate in ("fast5_w2_grad", "fast5_w2_hard",
                          "fast10_w1_grad"):
            key = candidate if suffix == "entry_bound" else f"{candidate}_{suffix}"
            results[f"{key}_vs_weekly_bootstrap"] = paired_bootstrap(
                pnl[key], baseline)
    frame = pd.DataFrame(results).T
    frame.to_csv(HERE / f"validation_{label}.csv")
    print(f"\n=== {label} ===")
    print(frame.to_string())
    return frame


if __name__ == "__main__":
    run_config(PRODUCTION, "production")
    run_config(D15, "d15")
