"""Build a causal, memory-bounded first-hour research panel.

The raw cache is roughly 810 MB. This builder processes one year at a time
and stores only declared snapshots, avoiding the memory pressure that killed
the downloader twice.

Important semantic: Databento ``ohlcv-1d`` is aggregated on UTC dates.
Consequently ``prev_utc_close`` is not the 16:00 official close. Gap-based
experiments retain it for audit compatibility and are labelled accordingly;
the principal new experiments use same-session minute features.
"""
from pathlib import Path

import numpy as np
import pandas as pd

BASE = Path(__file__).resolve().parent
DATA = BASE / "data"

OPEN_TIMES = (
    "09:30", "09:35", "09:36", "09:40", "09:41", "09:45",
    "09:46", "09:50", "10:00", "10:01", "10:10", "10:25", "10:30",
)
CLOSE_TIMES = (
    "09:34", "09:39", "09:44", "09:49", "09:59", "10:09",
    "10:14", "10:24", "10:29", "10:34",
)
LAST_TARGETS = ("09:49", "09:59", "10:09", "10:24", "10:25", "10:30")
SIGNAL_WINDOWS = {
    "5": ("09:30", "09:34"),
    "10": ("09:30", "09:39"),
    "15": ("09:30", "09:44"),
    "30": ("09:30", "09:59"),
}


def load_daily_context() -> pd.DataFrame:
    """Return point-in-time daily features known before the trading day."""
    daily = pd.read_parquet(DATA / "panel_daily.parquet").sort_values(
        ["raw_symbol", "day"]
    )
    g = daily.groupby("raw_symbol", sort=False)
    daily["prev_utc_close"] = g["close"].shift(1)
    daily["prev_obs_day"] = g["day"].shift(1)
    one_day_ret = g["close"].pct_change(fill_method=None)
    daily["prev_day_ret"] = one_day_ret.groupby(
        daily["raw_symbol"], sort=False
    ).shift(1)
    daily["prev_dv20"] = g["dv"].transform(
        lambda x: x.rolling(20, min_periods=10).median().shift(1)
    )
    return daily.set_index(["day", "raw_symbol"])[
        ["prev_utc_close", "prev_obs_day", "prev_day_ret", "prev_dv20"]
    ]


def one_year(year: str, universe: pd.DataFrame) -> pd.DataFrame:
    """Aggregate one raw minute file into causal per-name/day snapshots."""
    raw = pd.read_parquet(DATA / f"minutes_{year}.parquet")
    ids = pd.read_parquet(DATA / f"idmap_{year}.parquet")[
        ["day", "instrument_id", "raw_symbol"]
    ]
    raw["hm"] = (
        pd.to_datetime(raw["ts_event"], utc=True)
        .dt.tz_convert("America/New_York")
        .dt.strftime("%H:%M")
    )
    raw = raw[(raw["hm"] >= "09:30") & (raw["hm"] <= "10:34")]
    raw = raw.merge(
        ids, on=["day", "instrument_id"], how="left", validate="many_to_one"
    )
    raw["month"] = raw["day"].str[:7]
    raw = raw.merge(
        universe,
        left_on=["month", "raw_symbol"],
        right_on=["month", "symbol"],
        how="inner",
        validate="many_to_one",
    ).drop(columns="symbol")

    key = ["day", "raw_symbol"]
    out = pd.DataFrame(index=raw.groupby(key, sort=False).size().index)

    for hm in OPEN_TIMES:
        out[f"o{hm.replace(':', '')}"] = (
            raw[raw["hm"] == hm].set_index(key)["open"]
        )
    for hm in CLOSE_TIMES:
        out[f"c{hm.replace(':', '')}"] = (
            raw[raw["hm"] == hm].set_index(key)["close"]
        )
    # Honest fallback for a missing target-minute print: the last actual
    # XNAS trade at or before the target, matching the original engine.
    for hm in LAST_TARGETS:
        eligible = raw[(raw["hm"] >= "09:30") & (raw["hm"] <= hm)]
        out[f"x{hm.replace(':', '')}"] = (
            eligible.sort_values("ts_event")
            .groupby(key, sort=False)
            .tail(1)
            .set_index(key)["close"]
        )

    for label, (start, end) in SIGNAL_WINDOWS.items():
        w = raw[(raw["hm"] >= start) & (raw["hm"] <= end)]
        wg = w.groupby(key, sort=False)
        out[f"hi{label}"] = wg["high"].max()
        out[f"lo{label}"] = wg["low"].min()
        out[f"vol{label}"] = wg["volume"].sum()
        out[f"bars{label}"] = wg["hm"].nunique().astype("int16")

    out = out.reset_index().rename(columns={"raw_symbol": "sym"})
    tags = universe[["month", "symbol", "mega", "med_dv"]].rename(
        columns={"symbol": "sym"}
    )
    out["month"] = out["day"].str[:7]
    return out.merge(
        tags, on=["month", "sym"], how="left", validate="many_to_one"
    )


def add_features(panel: pd.DataFrame, daily_context: pd.DataFrame) -> pd.DataFrame:
    """Add only information known by the corresponding signal timestamp."""
    panel = panel.join(
        daily_context,
        on=["day", "sym"],
        how="left",
        validate="many_to_one",
    )
    panel = panel.sort_values(["sym", "day"]).reset_index(drop=True)

    for label, close_col in (
        ("5", "c0934"),
        ("10", "c0939"),
        ("15", "c0944"),
        ("30", "c0959"),
    ):
        panel[f"r{label}"] = panel[close_col] / panel["o0930"] - 1

    # Retained only to reproduce and diagnose the original experiment.
    panel["gap_utc"] = panel["o0930"] / panel["prev_utc_close"] - 1

    # Causal relative-volume surprise versus prior observed opening windows.
    for label in ("5", "10", "15", "30"):
        dv = panel[f"vol{label}"] * panel["o0930"]
        baseline = dv.groupby(panel["sym"], sort=False).transform(
            lambda x: x.rolling(20, min_periods=10).median().shift(1)
        )
        panel[f"log_rvol{label}"] = np.log(
            dv.clip(lower=1.0) / baseline.clip(lower=1.0)
        )

    for label in ("5", "10", "15", "30"):
        r = panel[f"r{label}"]
        panel[f"r{label}_med"] = r - r.groupby(panel["day"]).transform("median")
        panel[f"r{label}_rank"] = r.groupby(panel["day"]).rank(pct=True)
        panel[f"breadth{label}"] = (
            r.gt(0).groupby(panel["day"]).transform("mean")
        )
        panel[f"disp{label}"] = r.groupby(panel["day"]).transform(
            lambda x: x.quantile(0.75) - x.quantile(0.25)
        )
        rv = panel[f"log_rvol{label}"]
        panel[f"rvol{label}_rank"] = rv.groupby(panel["day"]).rank(pct=True)

    panel["gap_rank"] = panel.groupby("day")["gap_utc"].rank(pct=True)
    panel["gap_breadth"] = panel["gap_utc"].gt(0).groupby(
        panel["day"]
    ).transform("mean")
    return panel


def main() -> None:
    universe = pd.read_parquet(DATA / "universe.parquet")
    years = sorted(
        f.stem.rsplit("_", 1)[1] for f in DATA.glob("minutes_*.parquet")
    )
    frames = []
    for year in years:
        block = one_year(year, universe)
        frames.append(block)
        print(
            f"{year}: {len(block):,} name-days / "
            f"{block['day'].nunique()} sessions",
            flush=True,
        )
    panel = pd.concat(frames, ignore_index=True)
    panel = add_features(panel, load_daily_context())
    panel.to_parquet(DATA / "signal_panel.parquet", index=False)

    exact5 = int((panel["bars5"] == 5).sum())
    split_like = int(panel["gap_utc"].abs().gt(0.30).sum())
    print(
        f"saved {len(panel):,} name-days, {panel['day'].nunique()} sessions, "
        f"{panel['sym'].nunique()} symbols"
    )
    print(
        f"complete 5-minute windows: {exact5:,}/{len(panel):,}; "
        f"|UTC-gap| >30%: {split_like:,}"
    )


if __name__ == "__main__":
    main()
