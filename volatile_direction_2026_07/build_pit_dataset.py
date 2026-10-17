#!/usr/bin/env python3
"""Build a split-verified point-in-time equity panel from local XNAS files.

Observed price discontinuities are only candidates.  They are adjusted when
they appear in ``split_verification.csv`` as independently verified corporate
actions.  A candidate affecting any symbol that ever enters the PIT universe
causes the build to fail until it has been reviewed.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "opening_leader_2026_07" / "data"
OUT = Path(__file__).resolve().parent / "data"
VERIFICATION_PATH = Path(__file__).resolve().parent / "split_verification.csv"
COMMON_EQUITY_TYPES = {"C", "A", "O"}
SPLIT_RATIOS = np.array(
    [1.25, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0, 10.0, 20.0],
    dtype=float,
)


def read_year(year: int) -> pd.DataFrame:
    daily_path = SOURCE / f"daily_{year}.parquet"
    map_path = SOURCE / f"idmap_{year}.parquet"
    if not daily_path.exists() or not map_path.exists():
        raise FileNotFoundError(f"missing daily/idmap pair for {year}")
    daily = pd.read_parquet(
        daily_path,
        columns=["instrument_id", "open", "high", "low", "close", "volume"],
    ).reset_index()
    daily["day"] = pd.to_datetime(daily["ts_event"], utc=True).dt.date.astype(str)
    mapping = pd.read_parquet(map_path)
    mapping = mapping[mapping["security_type"].isin(COMMON_EQUITY_TYPES)]
    panel = daily.merge(
        mapping[["day", "instrument_id", "raw_symbol", "security_type"]],
        on=["day", "instrument_id"],
        how="inner",
        validate="many_to_one",
    )
    panel = panel[
        (panel["open"] > 0)
        & (panel["high"] > 0)
        & (panel["low"] > 0)
        & (panel["close"] > 0)
        & (panel["high"] >= panel["low"])
    ].copy()
    return panel[
        [
            "day",
            "raw_symbol",
            "security_type",
            "open",
            "high",
            "low",
            "close",
            "volume",
        ]
    ]


def nearest_split_factor(overnight_ratio: float) -> float:
    """Return prior-share/current-share conversion, or 1 when not split-like."""
    if not np.isfinite(overnight_ratio) or overnight_ratio <= 0:
        return 1.0
    candidates = np.concatenate((SPLIT_RATIOS, 1.0 / SPLIT_RATIOS))
    nearest = float(candidates[np.argmin(np.abs(np.log(overnight_ratio / candidates)))])
    if abs(np.log(overnight_ratio / nearest)) <= np.log(1.03):
        return nearest
    return 1.0


def candidate_factor(group: pd.DataFrame) -> pd.Series:
    """Identify mechanical split candidates without deciding they are splits."""
    previous_close = group["close"].shift(1)
    overnight_ratio = previous_close / group["open"]
    extreme = (group["open"] / previous_close - 1.0).abs() >= 0.30
    factor = pd.Series(1.0, index=group.index)
    factor.loc[extreme] = overnight_ratio.loc[extreme].map(nearest_split_factor)
    return factor


def load_verification() -> pd.DataFrame:
    required = {
        "day",
        "raw_symbol",
        "resolution",
        "verified_split_factor",
        "source",
    }
    if not VERIFICATION_PATH.exists():
        return pd.DataFrame(columns=sorted(required))
    verification = pd.read_csv(
        VERIFICATION_PATH,
        dtype={"day": str, "raw_symbol": str, "source": str},
    )
    missing = required - set(verification.columns)
    if missing:
        raise ValueError(
            f"{VERIFICATION_PATH} is missing columns: {sorted(missing)}"
        )
    if verification.duplicated(["day", "raw_symbol"]).any():
        raise ValueError("split verification contains duplicate symbol/day rows")
    verification["verified_split_factor"] = pd.to_numeric(
        verification["verified_split_factor"], errors="raise"
    )
    allowed = {"split", "news", "reset"}
    invalid = set(verification["resolution"]) - allowed
    if invalid:
        raise ValueError(f"invalid split-candidate resolutions: {sorted(invalid)}")
    if (verification["verified_split_factor"] <= 0).any():
        raise ValueError("verified split factors must be positive")
    non_splits = verification["resolution"] != "split"
    if (verification.loc[non_splits, "verified_split_factor"] != 1.0).any():
        raise ValueError("news/reset resolutions must use factor 1")
    return verification


def adjust_symbol(group: pd.DataFrame) -> pd.DataFrame:
    group = group.sort_values("day").copy()
    previous_close = group["close"].shift(1)
    factor = group["verified_split_factor"].fillna(1.0)
    adjusted_previous = previous_close / factor
    clean_return = group["close"] / adjusted_previous - 1.0
    clean_return.iloc[0] = np.nan

    # Reject impossible residual jumps rather than letting one bad definition
    # dominate volatility ranking and moving averages.
    bad_return = clean_return.abs() > 1.5
    clean_return.loc[bad_return] = np.nan
    synthetic_close = (1.0 + clean_return.fillna(0.0)).cumprod() * 100.0
    scale = synthetic_close / group["close"]

    group["split_factor"] = factor
    group["split_detected"] = group["candidate_split_factor"] != 1.0
    group["split_verified"] = factor != 1.0
    group["clean_return"] = clean_return
    group["adj_open"] = group["open"] * scale
    group["adj_high"] = group["high"] * scale
    group["adj_low"] = group["low"] * scale
    group["adj_close"] = synthetic_close
    group["dollar_volume"] = group["close"] * group["volume"]
    return group


def build_universe(panel: pd.DataFrame) -> pd.DataFrame:
    panel = panel.copy()
    panel["month"] = panel["day"].str[:7]
    months = sorted(panel["month"].unique())
    trading_days = panel.groupby("month")["day"].nunique()
    rows: list[dict] = []
    for previous, current in zip(months[:-1], months[1:]):
        block = panel[panel["month"] == previous]
        stats = block.groupby("raw_symbol", sort=False).agg(
            median_dollar_volume=("dollar_volume", "median"),
            observations=("day", "nunique"),
            last_close=("close", "last"),
        )
        eligible = stats[
            (stats["last_close"] >= 5.0)
            & (stats["observations"] >= 0.60 * trading_days[previous])
        ].nlargest(150, "median_dollar_volume")
        mega = set(eligible.nlargest(30, "median_dollar_volume").index)
        for rank, (symbol, values) in enumerate(eligible.iterrows(), start=1):
            rows.append(
                {
                    "month": current,
                    "symbol": symbol,
                    "rank": rank,
                    "mega": symbol in mega,
                    "median_dollar_volume": values["median_dollar_volume"],
                }
            )
    return pd.DataFrame(rows)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("years", nargs="+", type=int)
    args = parser.parse_args()
    years = sorted(set(args.years))
    OUT.mkdir(parents=True, exist_ok=True)
    pieces = []
    for year in years:
        piece = read_year(year)
        pieces.append(piece)
        print(f"{year}: {len(piece):,} mapped common-stock rows", flush=True)
    panel = pd.concat(pieces, ignore_index=True)
    panel = panel.sort_values(["raw_symbol", "day"]).drop_duplicates(
        ["raw_symbol", "day"], keep="last"
    )
    previous_day = panel.groupby("raw_symbol")["day"].shift(1)
    previous_close = panel.groupby("raw_symbol")["close"].shift(1)
    observation_gap_days = (
        pd.to_datetime(panel["day"]) - pd.to_datetime(previous_day)
    ).dt.days
    overnight_ratio = previous_close / panel["open"]
    extreme = (
        ((panel["open"] / previous_close - 1.0).abs() >= 0.30)
        & observation_gap_days.le(5)
    )
    panel["candidate_split_factor"] = 1.0
    panel.loc[extreme, "candidate_split_factor"] = overnight_ratio.loc[
        extreme
    ].map(nearest_split_factor)
    panel["dollar_volume"] = panel["close"] * panel["volume"]
    panel["observation_gap_days"] = observation_gap_days
    universe = build_universe(panel)
    relevant_symbols = set(universe["symbol"])
    candidates = panel[
        (panel["candidate_split_factor"] != 1.0)
        & panel["raw_symbol"].isin(relevant_symbols)
    ][
        [
            "day",
            "raw_symbol",
            "open",
            "close",
            "candidate_split_factor",
        ]
    ].copy()
    verification = load_verification()
    candidates = candidates.merge(
        verification,
        on=["day", "raw_symbol"],
        how="left",
        validate="one_to_one",
    )
    label = f"{years[0]}_{years[-1]}"
    candidate_path = OUT / f"split_candidates_{label}.csv"
    candidates.sort_values(["day", "raw_symbol"]).to_csv(candidate_path, index=False)
    unresolved = candidates["verified_split_factor"].isna()
    if unresolved.any():
        sample = candidates.loc[unresolved, ["day", "raw_symbol"]].head(20)
        raise RuntimeError(
            "unverified split candidates affect PIT-universe symbols; "
            f"review {candidate_path} before building:\n"
            + sample.to_string(index=False)
        )
    verified = verification[
        ["day", "raw_symbol", "resolution", "verified_split_factor"]
    ]
    panel = panel.merge(
        verified,
        on=["day", "raw_symbol"],
        how="left",
        validate="one_to_one",
    )
    panel["resolution"] = panel["resolution"].fillna("")
    panel["history_reset"] = (
        panel["observation_gap_days"].gt(5)
        | panel["resolution"].eq("reset")
        | panel["observation_gap_days"].isna()
    )
    panel["history_segment"] = panel.groupby("raw_symbol")[
        "history_reset"
    ].cumsum()
    panel = (
        panel.sort_values(["raw_symbol", "history_segment", "day"])
        .groupby(["raw_symbol", "history_segment"], group_keys=True)
        .apply(adjust_symbol, include_groups=False)
        .reset_index(drop=False)
    )
    panel = panel.drop(
        columns=[column for column in panel if column.startswith("level_")],
        errors="ignore",
    )
    panel_path = OUT / f"pit_panel_{label}.parquet"
    universe_path = OUT / f"pit_universe_{label}.parquet"
    panel.to_parquet(panel_path, index=False)
    universe.to_parquet(universe_path, index=False)
    print(
        f"panel={len(panel):,} rows {panel.day.min()}..{panel.day.max()} "
        f"symbols={panel.raw_symbol.nunique():,} "
        f"candidates={panel.split_detected.sum():,} "
        f"verified_splits={panel.split_verified.sum():,}"
    )
    print(
        f"universe={len(universe):,} rows months={universe.month.nunique()} "
        f"symbols={universe.symbol.nunique():,}"
    )
    print(panel_path)
    print(universe_path)
    print(candidate_path)


if __name__ == "__main__":
    main()
