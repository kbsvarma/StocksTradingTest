#!/usr/bin/env python3
"""Independent audit and frozen extension of the ridge-plus volume claim.

Research only. Reads the existing minute caches and writes only under this
directory. It does not import the daemon, broker, config, or state writers.
"""
from __future__ import annotations

import glob
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd


HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
OUT = HERE / "results"
OUT.mkdir(exist_ok=True)
sys.path[:0] = [
    str(ROOT / "quant_replacement_2026_07"),
    str(ROOT / "trend_bot"),
    str(ROOT / "trend_bot" / "research"),
    str(ROOT / "strategy_extension_2026_07"),
    str(ROOT / "adaptive_rotation_2026_07"),
]

import rotation_grid as rg  # noqa: E402
import strategy  # noqa: E402
from run_adaptive_rotation import (  # noqa: E402
    PRODUCTION,
    START,
    SYMS,
    baseline_policy,
)
from run_stage_a import (  # noqa: E402
    hourly_mtm,
    pnl_stats,
    roll_clean_log_returns,
    simulate_direction,
)
from run_stage_b import simulate_capped  # noqa: E402
from run_stage_c import (  # noqa: E402
    FEATURES,
    causal_predictions,
    feature_frames,
    fit_ridge,
    predict,
    teacher_trades,
)


DATA = {
    "NQ": ROOT / "event_edge_research/data/NQ_ohlcv-1m_*.parquet",
    "ES": ROOT / "event_edge_research/data/ES_ohlcv-1m_*.parquet",
    "YM": ROOT / "trend_replication_2026_07/data/YM_1m_*.parquet",
}
END_SELECTION = pd.Timestamp("2025-12-31 23:59:59", tz="UTC")
FRICTIONS = (3.5, 7.0, 14.0)
SEED = 20260727

PLAIN = FEATURES + ("rv_plain",)
HOW = FEATURES + ("rv_how",)
DIRECTED = FEATURES + ("rv_dir",)
EFFICIENT = FEATURES + ("rv_eff",)
INTERACT = FEATURES + ("rv_how", "rv_dir")
STATE = FEATURES + ("rv_how", "rv_pos", "rv_pos_t120")
NO_CLOCK_RV = tuple(x for x in FEATURES if x not in {"hour_sin", "hour_cos"}) + (
    "rv_how",
)

SPECS = {
    "R0": FEATURES,
    "RV_PLAIN": PLAIN,
    "RV_HOW": HOW,
    "RV_DIR": DIRECTED,
    "RV_EFF": EFFICIENT,
    "RV_INTERACT": INTERACT,
    "RV_STATE": STATE,
    "NO_CLOCK_RV": NO_CLOCK_RV,
}


def load_60m(symbol: str) -> pd.DataFrame:
    files = sorted(glob.glob(str(DATA[symbol])))
    if not files:
        raise FileNotFoundError(DATA[symbol])
    raw = pd.concat([pd.read_parquet(path) for path in files]).sort_index()
    raw = raw[~raw.index.duplicated(keep="last")]
    raw["roll_flag"] = raw["instrument_id"].ne(
        raw["instrument_id"].shift()).astype(np.int8)
    grouped = raw.resample("60min", label="left")
    bars = pd.DataFrame(
        {
            "open": grouped["open"].first(),
            "high": grouped["high"].max(),
            "low": grouped["low"].min(),
            "close": grouped["close"].last(),
            "volume": grouped["volume"].sum(min_count=1),
            "roll": grouped["roll_flag"].max(),
            "contract": grouped["instrument_id"].last(),
        }
    ).dropna(subset=["open", "high", "low", "close"])
    bars["roll"] = bars["roll"].fillna(0).astype(np.int8)
    return bars


def rolling_same_slot(series: pd.Series, slots: pd.Series, window: int,
                      minimum: int, function: str) -> pd.Series:
    frame = pd.DataFrame({"value": series, "slot": slots}, index=series.index)

    def one(group: pd.DataFrame) -> pd.Series:
        roll = group["value"].rolling(window, min_periods=minimum)
        return getattr(roll, function)().shift(1)

    return frame.groupby("slot", group_keys=False).apply(
        one, include_groups=False).reindex(series.index)


def add_volume_features(bars: dict[str, pd.DataFrame],
                        base: dict[str, pd.DataFrame]):
    result = {symbol: frame.copy() for symbol, frame in base.items()}
    diagnostics = []
    for symbol in SYMS:
        market = bars[symbol]
        volume = market["volume"].replace(0.0, np.nan)
        log_volume = np.log(volume)

        plain_median = volume.rolling(
            480, min_periods=240).median().shift(1)
        rv_plain = np.log(volume / plain_median).clip(-3.0, 3.0)

        local = market.index.tz_convert("America/New_York")
        slots = pd.Series(local.dayofweek * 24 + local.hour,
                          index=market.index)
        slot_center = rolling_same_slot(
            log_volume, slots, 26, 13, "median")
        raw_surprise = log_volume - slot_center
        slot_bias = rolling_same_slot(
            raw_surprise, slots, 26, 13, "median")
        absolute_residual = (raw_surprise - slot_bias).abs()
        slot_mad = rolling_same_slot(
            absolute_residual, slots, 26, 13, "median")
        rv_how = ((raw_surprise - slot_bias)
                  / (1.4826 * slot_mad).replace(0.0, np.nan)).clip(-5.0, 5.0)

        returns = roll_clean_log_returns(market)
        rv_dir = rv_how * np.sign(returns)
        rv_eff = (rv_how * np.sign(result[symbol]["t120"])
                  * result[symbol]["eff120"])
        rv_pos = rv_how.clip(lower=0.0)
        rv_pos_t120 = rv_pos * result[symbol]["t120"].clip(lower=0.0)

        additions = {
            "rv_plain": rv_plain,
            "rv_how": rv_how,
            "rv_dir": rv_dir,
            "rv_eff": rv_eff,
            "rv_pos": rv_pos,
            "rv_pos_t120": rv_pos_t120,
        }
        for name, values in additions.items():
            result[symbol][name] = values

        for source, group in market.groupby(
                np.where(market.index <= END_SELECTION, "selection", "2026")):
            diagnostics.append(
                {
                    "symbol": symbol,
                    "period": source,
                    "bars": len(group),
                    "roll_bars": int(group["roll"].sum()),
                    "volume_zero": int(group["volume"].fillna(0).eq(0).sum()),
                    "volume_median": float(group["volume"].median()),
                    "volume_p99": float(group["volume"].quantile(0.99)),
                    "contracts": int(group["contract"].nunique()),
                }
            )
    return result, pd.DataFrame(diagnostics)


def extended_teachers(bars, features, names=None) -> pd.DataFrame:
    all_names = (sorted(set().union(*SPECS.values()))
                 if names is None else list(names))
    rows = []
    for symbol in SYMS:
        # Use the corrected signed-roll reference teacher. The archived
        # run_stage_c.teacher_trades path calls simulate_direction(), whose
        # roll branch applies abs(points) and turns 11 losing roll exits into
        # positive labels. The shipped shadow teacher and Claude's Ridge-plus
        # scratch program both use simulate_weighted(), which keeps the sign.
        trades = rg.simulate_weighted(
            bars[symbol], symbol, lambda stamp: 1)
        atr = strategy.signal_frame(bars[symbol], PRODUCTION)["atr"]
        index = bars[symbol].index
        for trade in trades.itertuples():
            entry_i = index.get_loc(trade.entry_time)
            signal_i = entry_i - 1
            if signal_i < 0:
                continue
            feature = features[symbol].iloc[signal_i]
            base_values = feature[list(FEATURES)].to_numpy(dtype=float)
            if not np.isfinite(base_values).all():
                continue
            risk = (PRODUCTION["k_trail"] * float(atr.iloc[signal_i])
                    * rg.MULT[symbol])
            net = trade.points * rg.MULT[symbol] - rg.FR
            rows.append(
                {
                    "signal_time": index[signal_i],
                    "exit_time": trade.exit_time,
                    "sym": symbol,
                    "y": net / risk,
                    **feature[all_names].to_dict(),
                }
            )
    return pd.DataFrame(rows).sort_values(
        "exit_time").reset_index(drop=True)


def generic_predictions(bars, features, teachers, names) -> dict[str, pd.Series]:
    names = tuple(names)
    base = {
        symbol: strategy.signal_frame(
            bars[symbol], PRODUCTION)["signal"].astype(np.int8)
        for symbol in SYMS
    }
    output = {
        symbol: pd.Series(0, index=bars[symbol].index, dtype=np.int8)
        for symbol in SYMS
    }
    months = pd.date_range(
        START.normalize(),
        max(frame.index.max() for frame in bars.values()),
        freq="MS",
        tz="UTC",
    )
    clean_teacher = teachers.dropna(subset=list(names))
    for month in months:
        next_month = month + pd.offsets.MonthBegin(1)
        train = clean_teacher[clean_teacher["exit_time"] < month]
        blocks = []
        for symbol in SYMS:
            mask = (
                (bars[symbol].index >= month)
                & (bars[symbol].index < next_month)
                & base[symbol].astype(bool)
            )
            block = features[symbol].loc[mask, list(names)].copy()
            block["sym"] = symbol
            block["stamp"] = block.index
            blocks.append(block)
        decisions = pd.concat(blocks).dropna(subset=list(names))
        if decisions.empty:
            continue
        if len(train) < 500:
            for row in decisions.itertuples():
                output[row.sym].loc[row.stamp] = 1
            continue
        model = fit_ridge(train, 10.0, feature_names=names)
        means, _ = predict(model, decisions)
        for row, mean in zip(decisions.itertuples(), means):
            if mean > 0.0:
                output[row.sym].loc[row.stamp] = 1
    return output


def policy_and_base(bars):
    daily = {
        symbol: frame["close"].resample("1D").last().dropna()
        for symbol, frame in bars.items()
    }
    common = bars[SYMS[0]].index
    for symbol in SYMS[1:]:
        common = common.intersection(bars[symbol].index)
    return baseline_policy(daily, common), {
        symbol: strategy.signal_frame(
            bars[symbol], PRODUCTION)["signal"].astype(np.int8)
        for symbol in SYMS
    }


def simulate_variant(directions, policy, bars):
    return simulate_capped(directions, policy, bars, 3)


def path_stats(pnl: pd.Series, trades: pd.DataFrame, cutoff=END_SELECTION):
    selected = pnl[pnl.index <= cutoff].sort_index()
    equity = selected.cumsum()
    drawdown = equity - equity.cummax().clip(lower=0.0)
    daily = selected.groupby(selected.index.normalize()).sum()
    yearly = selected.groupby(selected.index.year).sum()
    eras = {
        "2011_15": float(selected[selected.index.year <= 2015].sum()),
        "2016_20": float(selected[
            (selected.index.year >= 2016) & (selected.index.year <= 2020)
        ].sum()),
        "2021_25": float(selected[selected.index.year >= 2021].sum()),
    }
    selected_trades = trades[trades["exit_time"] <= cutoff]
    exit_net = (
        selected_trades["points"]
        * selected_trades["sym"].map(rg.MULT)
        - rg.FR
    ) * selected_trades["qty"]
    exit_eq = exit_net.cumsum()
    exit_dd = exit_eq - exit_eq.cummax().clip(lower=0.0)
    standard_deviation = daily.std(ddof=1)
    return {
        "net_2011_25": float(selected.sum()),
        "maxDD_hourly": float(drawdown.min()),
        "maxDD_exit": float(exit_dd.min()),
        "pdd_hourly": float(selected.sum() / abs(drawdown.min())),
        "worst_year": float(yearly.min()),
        "worst_year_label": int(yearly.idxmin()),
        "sharpe_daily": float(
            daily.mean() / standard_deviation * np.sqrt(252)
        ) if standard_deviation > 0 else np.nan,
        "trades_2011_25": int(len(selected_trades)),
        "contracts_2011_25": int(selected_trades["qty"].sum()),
        **eras,
    }


def attribution(trades, bars, cutoff=END_SELECTION):
    rows = []
    for symbol in SYMS:
        subset = trades[(trades["sym"] == symbol)
                        & (trades["exit_time"] <= cutoff)]
        if subset.empty:
            net = 0.0
        else:
            net = float(hourly_mtm(subset, bars, 3.5).sum())
        rows.append({"symbol": symbol, "net": net})
    return pd.DataFrame(rows)


def moving_block_bootstrap(delta: pd.Series, block: int, replications=10_000):
    values = delta.fillna(0.0).to_numpy(dtype=float)
    n = len(values)
    blocks = int(np.ceil(n / block))
    random = np.random.default_rng(SEED + block)
    totals = np.empty(replications)
    offsets = np.arange(block)
    for left in range(0, replications, 200):
        right = min(left + 200, replications)
        starts = random.integers(0, n, size=(right - left, blocks, 1))
        indices = (starts + offsets) % n
        totals[left:right] = values[
            indices.reshape(right - left, -1)[:, :n]
        ].sum(axis=1)
    return {
        "block": block,
        "observed": float(values.sum()),
        "p_nonpositive": float(np.mean(totals <= 0)),
        "lower_90": float(np.quantile(totals, 0.05)),
        "lower_95": float(np.quantile(totals, 0.025)),
        "upper_95": float(np.quantile(totals, 0.975)),
    }


def exposure(trades: pd.DataFrame) -> int:
    events = []
    for trade in trades.itertuples():
        events.extend(
            (
                (trade.entry_time, 1, int(trade.qty)),
                (trade.exit_time, 0, -int(trade.qty)),
            )
        )
    if not events:
        return 0
    frame = pd.DataFrame(events, columns=["stamp", "order", "change"])
    frame = frame.sort_values(["stamp", "order"])
    return int(frame["change"].cumsum().max())


def shadow_replay(directions, policy, bars):
    """Replicate the scoreboard's independent-per-symbol (not joint-cap) FSM."""
    pieces = []
    for symbol in SYMS:
        quantity = policy[symbol]

        def requested(stamp, s=quantity):
            return int(s.asof(stamp)) if stamp >= s.index[0] else 1

        pieces.append(
            simulate_direction(
                bars[symbol], symbol, directions[symbol],
                config=PRODUCTION, quantity=requested)
        )
    return pd.concat(pieces).sort_values("exit_time").reset_index(drop=True)


def main():
    rg.START, rg.SCFG = START, dict(PRODUCTION)
    print("loading and resampling minute caches", flush=True)
    bars = {symbol: load_60m(symbol) for symbol in SYMS}
    data_rows = []
    for symbol, frame in bars.items():
        data_rows.append(
            {
                "symbol": symbol,
                "start": frame.index.min(),
                "end": frame.index.max(),
                "bars": len(frame),
                "contracts": frame["contract"].nunique(),
                "roll_bars": int(frame["roll"].sum()),
                "zero_volume": int(frame["volume"].fillna(0).eq(0).sum()),
            }
        )
    pd.DataFrame(data_rows).to_csv(OUT / "data_inventory.csv", index=False)

    print("constructing features and teachers", flush=True)
    original_features = feature_frames(bars)
    features, volume_diagnostics = add_volume_features(
        bars, original_features)
    volume_diagnostics.to_csv(
        OUT / "volume_diagnostics.csv", index=False)
    original_teacher = teacher_trades(bars, original_features)
    teachers = extended_teachers(bars, features)
    original_teacher.to_parquet(
        OUT / "teacher_r0_archived_abs_roll_bug.parquet", index=False)
    teachers.to_parquet(OUT / "teacher_extended.parquet", index=False)
    label_audit = original_teacher[["exit_time", "sym", "y"]].rename(
        columns={"y": "archived_y"}).copy()
    label_audit["corrected_y"] = teachers["y"].to_numpy()
    label_audit["difference"] = (
        label_audit["corrected_y"] - label_audit["archived_y"])
    label_audit[label_audit["difference"].abs() > 1e-12].to_csv(
        OUT / "roll_teacher_label_corrections.csv", index=False)
    policy, base = policy_and_base(bars)

    print("running corrected R0 and frozen extension cells", flush=True)
    archived_r0 = causal_predictions(
        bars, original_features, original_teacher, penalties=(10.0,)
    )["ridge10_mean"]
    corrected_r0 = generic_predictions(
        bars, features, teachers, FEATURES)
    directions = {
        "V1": base,
        "R0_ARCHIVED_ABS_ROLL_BUG": archived_r0,
        "R0": corrected_r0,
    }
    for label, names in SPECS.items():
        if label == "R0":
            continue
        print(f"  {label}", flush=True)
        directions[label] = generic_predictions(
            bars, features, teachers, names)

    result_rows = []
    paths = {}
    trades_by_name = {}
    attribution_rows = []
    for label, direction in directions.items():
        print(f"simulating {label}", flush=True)
        trades = simulate_variant(direction, policy, bars)
        trades_by_name[label] = trades
        pnl = hourly_mtm(trades, bars, 3.5)
        paths[label] = pnl.groupby(pnl.index.normalize()).sum()
        stats = path_stats(pnl, trades)
        stats.update(
            {
                "cell": label,
                "max_exposure": exposure(trades),
                "net_full": float(pnl.sum()),
            }
        )
        for friction in FRICTIONS:
            stressed = hourly_mtm(trades, bars, friction)
            stats[f"net_friction_{friction:g}"] = float(
                stressed[stressed.index <= END_SELECTION].sum())
        result_rows.append(stats)
        attr = attribution(trades, bars)
        attr["cell"] = label
        attribution_rows.append(attr)
        trades.to_parquet(OUT / f"trades_{label}.parquet", index=False)

    results = pd.DataFrame(result_rows).set_index("cell").sort_index()
    results.to_csv(OUT / "extension_results.csv")
    pd.concat(attribution_rows, ignore_index=True).to_csv(
        OUT / "symbol_attribution.csv", index=False)
    daily = pd.concat(paths, axis=1).fillna(0.0)
    daily.to_parquet(OUT / "daily_pnl.parquet")

    bootstrap_rows = []
    for label in results.index:
        if label in {"V1", "R0"}:
            continue
        delta = daily[label].sub(daily["R0"], fill_value=0.0)
        delta = delta[delta.index <= END_SELECTION]
        for block in (63, 252):
            bootstrap_rows.append(
                {"cell": label, **moving_block_bootstrap(delta, block)}
            )
    pd.DataFrame(bootstrap_rows).to_csv(
        OUT / "paired_bootstrap.csv", index=False)

    # Reproduce the deployed scoreboard's two structural deviations:
    # independent symbol simulations (no joint cap) and zero historical rolls.
    shadow_rows = []
    for roll_mode in ("correct", "zero"):
        altered = {symbol: frame.copy() for symbol, frame in bars.items()}
        if roll_mode == "zero":
            for frame in altered.values():
                frame["roll"] = 0
        old_scfg = dict(rg.SCFG)
        rg.SCFG = dict(PRODUCTION)
        altered_original = feature_frames(altered)
        altered_features, _ = add_volume_features(
            altered, altered_original)
        altered_teachers = extended_teachers(altered, altered_features)
        altered_policy, _ = policy_and_base(altered)
        for label, names in (("R0", FEATURES), ("RV_PLAIN", PLAIN)):
            predicted = generic_predictions(
                altered, altered_features, altered_teachers, names)
            joint = simulate_variant(predicted, altered_policy, altered)
            separate = shadow_replay(predicted, altered_policy, altered)
            for engine, trades in (("joint_cap3", joint),
                                   ("independent", separate)):
                pnl = hourly_mtm(trades, altered, 3.5)
                shadow_rows.append(
                    {
                        "roll_mode": roll_mode,
                        "engine": engine,
                        "cell": label,
                        "net_full": float(pnl.sum()),
                        "max_exposure": exposure(trades),
                        "trades": len(trades),
                        "contracts": int(trades["qty"].sum()),
                    }
                )
        rg.SCFG = old_scfg
    pd.DataFrame(shadow_rows).to_csv(
        OUT / "shadow_structure_audit.csv", index=False)

    # Causality check for the shipped R2 feature itself.
    cutoff = pd.Timestamp("2020-01-01", tz="UTC")
    mutated = {symbol: frame.copy() for symbol, frame in bars.items()}
    random = np.random.default_rng(SEED)
    for frame in mutated.values():
        future = frame.index >= cutoff
        frame.loc[future, "volume"] *= random.lognormal(
            mean=0.0, sigma=3.0, size=int(future.sum()))
    mutated_base = feature_frames(mutated)
    mutated_features, _ = add_volume_features(mutated, mutated_base)
    mutated_teachers = extended_teachers(mutated, mutated_features)
    mutated_pred = generic_predictions(
        mutated, mutated_features, mutated_teachers, PLAIN)
    causality = {}
    for symbol in SYMS:
        before = directions["RV_PLAIN"][symbol].loc[
            directions["RV_PLAIN"][symbol].index < cutoff]
        after = mutated_pred[symbol].reindex(before.index)
        causality[symbol] = {
            "pre_cutoff_rows": len(before),
            "different": int(before.ne(after).sum()),
        }
    (OUT / "causality.json").write_text(
        json.dumps(causality, indent=2) + "\n")

    summary = {
        "selection_end": str(END_SELECTION),
        "registered_cells": list(SPECS),
        "r0_exact_vs_archived_full": (
            float(results.loc["R0", "net_full"]) - 69663.42499999987
        ),
        "causality": causality,
    }
    (OUT / "run_summary.json").write_text(
        json.dumps(summary, indent=2) + "\n")
    print(results.to_string(float_format=lambda value: f"{value:,.3f}"))
    print(f"wrote {OUT}", flush=True)


if __name__ == "__main__":
    main()
