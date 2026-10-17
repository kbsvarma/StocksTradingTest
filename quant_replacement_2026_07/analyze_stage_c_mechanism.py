"""Walk-forward calibration and mechanism diagnostics for Stage C."""
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

from run_adaptive_rotation import START  # noqa: E402
from run_stage_c import fit_ridge, predict  # noqa: E402


def walk_forward_scores(teachers: pd.DataFrame) -> pd.DataFrame:
    scored = []
    end = teachers["signal_time"].max()
    months = pd.date_range(START.normalize(), end, freq="MS", tz="UTC")
    for month in months:
        next_month = month + pd.offsets.MonthBegin(1)
        train = teachers[teachers["exit_time"] < month]
        test = teachers[(teachers["signal_time"] >= month)
                        & (teachers["signal_time"] < next_month)].copy()
        if len(train) < 500 or test.empty:
            continue
        mean, evidence = predict(fit_ridge(train, 10.0), test)
        test["predicted_y"] = mean
        test["evidence"] = evidence
        test["train_observations"] = len(train)
        scored.append(test)
    return pd.concat(scored).sort_values("signal_time").reset_index(drop=True)


def calibration(scored: pd.DataFrame, groups=10) -> pd.DataFrame:
    out = scored.copy()
    out["prediction_bucket"] = pd.qcut(
        out["predicted_y"], groups, labels=False, duplicates="drop") + 1
    return out.groupby("prediction_bucket").agg(
        observations=("y", "size"),
        predicted_mean=("predicted_y", "mean"),
        realized_mean=("y", "mean"),
        realized_median=("y", "median"),
        positive_share=("y", lambda x: float((x > 0).mean())),
        realized_sum=("y", "sum"),
    ).reset_index()


def grouped_edge(scored: pd.DataFrame, column: str) -> pd.DataFrame:
    return scored.groupby(column).agg(
        observations=("y", "size"),
        accepted=("predicted_y", lambda x: int((x > 0).sum())),
        predicted_mean=("predicted_y", "mean"),
        realized_mean=("y", "mean"),
        realized_sum=("y", "sum"),
    ).reset_index()


def conditional_edge(scored: pd.DataFrame, column: str) -> pd.DataFrame:
    return scored.groupby([column, "accepted"]).agg(
        observations=("y", "size"),
        predicted_mean=("predicted_y", "mean"),
        realized_mean=("y", "mean"),
        realized_sum=("y", "sum"),
    ).reset_index()


def run():
    teachers = pd.read_parquet(HERE / "stage_c_teacher.parquet")
    scored = walk_forward_scores(teachers)
    scored["accepted"] = scored["predicted_y"] > 0.0
    scored["era"] = np.select(
        [scored["signal_time"].dt.year <= 2015,
         scored["signal_time"].dt.year <= 2020],
        ["2011_15", "2016_20"], default="2021_end")
    scored["hour_et"] = scored["signal_time"].dt.tz_convert(
        "America/New_York").dt.hour

    buckets = calibration(scored)
    accepted = scored.groupby("accepted").agg(
        observations=("y", "size"),
        predicted_mean=("predicted_y", "mean"),
        realized_mean=("y", "mean"),
        realized_median=("y", "median"),
        positive_share=("y", lambda x: float((x > 0).mean())),
        realized_sum=("y", "sum"),
    ).reset_index()
    by_era = grouped_edge(scored, "era")
    by_symbol = grouped_edge(scored, "sym")
    by_hour = grouped_edge(scored, "hour_et")
    era_condition = conditional_edge(scored, "era")
    symbol_condition = conditional_edge(scored, "sym")

    rank_correlation = float(scored[["predicted_y", "y"]].corr(
        method="spearman").iloc[0, 1])
    summary = pd.DataFrame([{
        "observations": len(scored),
        "spearman_prediction_outcome": rank_correlation,
        "accepted_share": float(scored["accepted"].mean()),
        "accepted_realized_mean": float(scored.loc[scored.accepted, "y"].mean()),
        "rejected_realized_mean": float(scored.loc[~scored.accepted, "y"].mean()),
        "top_decile_realized_mean": float(buckets.iloc[-1]["realized_mean"]),
        "bottom_decile_realized_mean": float(buckets.iloc[0]["realized_mean"]),
    }])

    scored.to_parquet(HERE / "stage_d_scored_teacher.parquet", index=False)
    buckets.to_csv(HERE / "stage_d_calibration.csv", index=False)
    accepted.to_csv(HERE / "stage_d_accept_reject_edge.csv", index=False)
    by_era.to_csv(HERE / "stage_d_calibration_by_era.csv", index=False)
    by_symbol.to_csv(HERE / "stage_d_calibration_by_symbol.csv", index=False)
    by_hour.to_csv(HERE / "stage_d_calibration_by_hour.csv", index=False)
    era_condition.to_csv(
        HERE / "stage_d_accept_reject_by_era.csv", index=False)
    symbol_condition.to_csv(
        HERE / "stage_d_accept_reject_by_symbol.csv", index=False)
    summary.to_csv(HERE / "stage_d_mechanism_summary.csv", index=False)

    for title, frame in (
        ("SUMMARY", summary), ("CALIBRATION", buckets),
        ("ACCEPTED VS REJECTED", accepted), ("BY ERA", by_era),
        ("BY SYMBOL", by_symbol), ("BY HOUR", by_hour),
        ("ACCEPT/REJECT BY ERA", era_condition),
        ("ACCEPT/REJECT BY SYMBOL", symbol_condition),
    ):
        print(f"\n{title}")
        print(frame.to_string(index=False,
              float_format=lambda x: f"{x:,.4f}"))
    return scored


if __name__ == "__main__":
    run()
