"""Frozen 180-cell first-hour extension search.

The manifest is written before any outcome is evaluated. Only in-sample
survivors are evaluated on the 2024+ research holdout.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import erfc, sqrt
from pathlib import Path

import numpy as np
import pandas as pd

BASE = Path(__file__).resolve().parent
DATA = BASE / "data"
IS_END = "2023-12-31"
HOLDOUT_START = "2024-01-01"

ENTRY = {"5": "o0936", "10": "o0941", "15": "o0946", "30": "o1001"}
TARGET = {"0949": "x0949", "0959": "x0959", "1009": "x1009", "1024": "x1024"}


@dataclass
class Outcome:
    pnl: pd.Series
    active: pd.Series


def manifest() -> pd.DataFrame:
    rows: list[dict] = []

    # W1: 2 features x 10 deciles x long/short = 40.
    for feature in ("r5_rank", "gap_rank"):
        for decile in range(10):
            for side in (1, -1):
                rows.append(
                    dict(
                        wave="W1", kind="decile", feature=feature,
                        horizon="5", selector=f"d{decile}",
                        side=side, orientation="", threshold=np.nan,
                        state="", entry="o0936", exit="x1024",
                        portfolio="one_sided_decile",
                    )
                )

    # W2: 4 horizons x leader/loser x continuation/reversal = 16.
    for horizon in ("5", "10", "15", "30"):
        for selector in ("leader", "loser"):
            for orientation in ("continuation", "reversal"):
                rows.append(
                    dict(
                        wave="W2", kind="one", feature=f"r{horizon}",
                        horizon=horizon, selector=selector, side=0,
                        orientation=orientation, threshold=np.nan, state="",
                        entry=ENTRY[horizon], exit="x1024",
                        portfolio="single",
                    )
                )

    # W3: 3 horizons x 2 gap sides x confirm/reject x follow/fade = 24.
    for horizon in ("5", "10", "15"):
        for selector in ("gap_up", "gap_down"):
            for state in ("confirm", "reject"):
                for orientation in ("follow_gap", "fade_gap"):
                    rows.append(
                        dict(
                            wave="W3", kind="gap_interaction",
                            feature="gap_utc", horizon=horizon,
                            selector=selector, side=0,
                            orientation=orientation, threshold=0.30,
                            state=state, entry=ENTRY[horizon], exit="x1024",
                            portfolio="single",
                        )
                    )

    # W4: 3 horizons x leader/loser x cont/rev x median/top-quartile = 24.
    for horizon in ("5", "10", "15"):
        for selector in ("leader", "loser"):
            for orientation in ("continuation", "reversal"):
                for threshold in (0.50, 0.75):
                    rows.append(
                        dict(
                            wave="W4", kind="volume_one",
                            feature=f"r{horizon}", horizon=horizon,
                            selector=selector, side=0,
                            orientation=orientation, threshold=threshold,
                            state="", entry=ENTRY[horizon], exit="x1024",
                            portfolio="single",
                        )
                    )

    # W5: four base directions x four independent day-state halves = 16.
    for selector in ("leader", "loser"):
        for orientation in ("continuation", "reversal"):
            for state in (
                "breadth_high", "breadth_low", "dispersion_high",
                "dispersion_low",
            ):
                rows.append(
                    dict(
                        wave="W5", kind="state_one", feature="r5",
                        horizon="5", selector=selector, side=0,
                        orientation=orientation, threshold=np.nan,
                        state=state, entry="o0936", exit="x1024",
                        portfolio="single",
                    )
                )

    # W6 adds only the three early exits; 10:24 duplicates W2.
    for horizon in ("5", "10", "15"):
        for selector in ("leader", "loser"):
            for orientation in ("continuation", "reversal"):
                for target in ("0949", "0959", "1009"):
                    rows.append(
                        dict(
                            wave="W6", kind="one", feature=f"r{horizon}",
                            horizon=horizon, selector=selector, side=0,
                            orientation=orientation, threshold=np.nan,
                            state="", entry=ENTRY[horizon],
                            exit=TARGET[target], portfolio="single",
                        )
                    )

    # W7: 3 horizons x (decile + K=3/5/10) x mom/reversal = 24.
    for horizon in ("5", "10", "15"):
        for selector in ("decile", "k3", "k5", "k10"):
            for orientation in ("momentum", "reversal"):
                rows.append(
                    dict(
                        wave="W7", kind="neutral", feature=f"r{horizon}",
                        horizon=horizon, selector=selector, side=0,
                        orientation=orientation, threshold=np.nan, state="",
                        entry=ENTRY[horizon], exit="x1024",
                        portfolio="dollar_neutral",
                    )
                )

    out = pd.DataFrame(rows)
    out.insert(
        0,
        "cell_id",
        [f"{wave}_{i:03d}" for i, wave in enumerate(out["wave"], start=1)],
    )
    assert len(out) == 180, len(out)
    signature = [
        "kind", "feature", "horizon", "selector", "side", "orientation",
        "threshold", "state", "entry", "exit", "portfolio",
    ]
    assert not out.duplicated(signature).any()
    return out


def side_for(selector: str, orientation: str) -> int:
    """Return +1 long or -1 short for one-name return cells."""
    continuation = 1 if selector == "leader" else -1
    return continuation if orientation == "continuation" else -continuation


def leg_pnl(
    legs: pd.DataFrame,
    side: int,
    entry_col: str,
    exit_col: str,
    gross_budget: float,
    spread_mult: float,
) -> Outcome:
    """Equal-weight a set of same-side legs within each day."""
    if legs.empty:
        empty = pd.Series(dtype=float)
        return Outcome(empty, empty.astype(bool))
    x = legs.dropna(subset=[entry_col, exit_col]).copy()
    x = x[(x[entry_col] > 0) & (x[exit_col] > 0)]
    if x.empty:
        empty = pd.Series(dtype=float)
        return Outcome(empty, empty.astype(bool))
    count = x.groupby("day")["sym"].transform("size")
    notional = gross_budget / count
    hs = np.where(x["mega"], 0.0003, 0.0005) * spread_mult
    entry = x[entry_col].to_numpy(float)
    exit_px = x[exit_col].to_numpy(float)
    if side == 1:
        fill_entry = entry * (1 + hs)
        fill_exit = exit_px * (1 - hs)
        shares = notional.to_numpy(float) / fill_entry
        pnl = shares * (fill_exit - fill_entry) - 2 * shares * 0.005
    else:
        fill_entry = entry * (1 - hs)
        fill_exit = exit_px * (1 + hs)
        shares = notional.to_numpy(float) / fill_entry
        pnl = shares * (fill_entry - fill_exit) - 2 * shares * 0.005
    daily = pd.Series(pnl, index=x["day"]).groupby(level=0).sum()
    active = pd.Series(True, index=daily.index)
    return Outcome(daily, active)


def merge_outcomes(left: Outcome, right: Outcome) -> Outcome:
    idx = left.pnl.index.union(right.pnl.index)
    pnl = left.pnl.reindex(idx, fill_value=0) + right.pnl.reindex(idx, fill_value=0)
    active = (
        left.active.reindex(idx, fill_value=False)
        | right.active.reindex(idx, fill_value=False)
    )
    return Outcome(pnl, active)


def choose_extreme(x: pd.DataFrame, feature: str, selector: str) -> pd.DataFrame:
    x = x.dropna(subset=[feature]).sort_values(
        ["day", feature, "sym"],
        ascending=[True, selector == "loser", True],
    )
    return x.drop_duplicates("day", keep="first")


def evaluate_cell(
    p: pd.DataFrame, row: pd.Series, spread_mult: float = 1.0
) -> Outcome:
    entry, exit_col = row["entry"], row["exit"]
    # Entry existence is observable at order time. Exit uses an honest
    # last-print fallback, so it should exist whenever entry exists.
    x = p[p[entry].notna()].copy()
    kind = row["kind"]

    if kind == "decile":
        feature = row["feature"]
        x = x.dropna(subset=[feature])
        if feature == "gap_rank":
            x = x[x["gap_utc"].abs() <= 0.30]
        dec = np.minimum((x[feature] * 10).astype(int), 9)
        x = x[dec == int(row["selector"][1:])]
        return leg_pnl(
            x, int(row["side"]), entry, exit_col, 10_000, spread_mult
        )

    if kind == "one":
        chosen = choose_extreme(x, row["feature"], row["selector"])
        return leg_pnl(
            chosen,
            side_for(row["selector"], row["orientation"]),
            entry,
            exit_col,
            10_000,
            spread_mult,
        )

    if kind == "gap_interaction":
        horizon = row["horizon"]
        up = row["selector"] == "gap_up"
        x = x[
            x["gap_utc"].notna()
            & x[f"r{horizon}"].notna()
            & (x["gap_utc"].abs() <= float(row["threshold"]))
        ]
        x = x[x["gap_utc"].gt(0) if up else x["gap_utc"].lt(0)]
        confirming = x[f"r{horizon}"].gt(0) if up else x[f"r{horizon}"].lt(0)
        x = x[confirming if row["state"] == "confirm" else ~confirming]
        x = choose_extreme(
            x, "gap_utc", "leader" if up else "loser"
        )
        gap_side = 1 if up else -1
        side = gap_side if row["orientation"] == "follow_gap" else -gap_side
        return leg_pnl(x, side, entry, exit_col, 10_000, spread_mult)

    if kind == "volume_one":
        horizon = row["horizon"]
        x = x[x[f"rvol{horizon}_rank"] > float(row["threshold"])]
        chosen = choose_extreme(x, row["feature"], row["selector"])
        return leg_pnl(
            chosen,
            side_for(row["selector"], row["orientation"]),
            entry,
            exit_col,
            10_000,
            spread_mult,
        )

    if kind == "state_one":
        state = row["state"]
        if state == "breadth_high":
            x = x[x["breadth5"] > 0.5]
        elif state == "breadth_low":
            x = x[x["breadth5"] <= 0.5]
        elif state == "dispersion_high":
            x = x[x["disp5"] > x["disp5_ref"]]
        else:
            x = x[x["disp5"] <= x["disp5_ref"]]
        chosen = choose_extreme(x, "r5", row["selector"])
        return leg_pnl(
            chosen,
            side_for(row["selector"], row["orientation"]),
            entry,
            exit_col,
            10_000,
            spread_mult,
        )

    if kind == "neutral":
        feature = row["feature"]
        base = x.dropna(subset=[feature])
        if row["selector"] == "decile":
            ranks = base.groupby("day")[feature].rank(pct=True)
            top, bottom = base[ranks > 0.9], base[ranks <= 0.1]
        else:
            k = int(row["selector"][1:])
            ordered = base.sort_values(["day", feature, "sym"])
            bottom = ordered.groupby("day", sort=False).head(k)
            top = ordered.groupby("day", sort=False).tail(k)
        if row["orientation"] == "momentum":
            long_set, short_set = top, bottom
        else:
            long_set, short_set = bottom, top
        return merge_outcomes(
            leg_pnl(long_set, 1, entry, exit_col, 5_000, spread_mult),
            leg_pnl(short_set, -1, entry, exit_col, 5_000, spread_mult),
        )
    raise ValueError(f"unknown kind {kind}")


def hac_t(x: pd.Series, lags: int = 5) -> float:
    a = x.to_numpy(float)
    if len(a) < 2 or np.std(a, ddof=1) == 0:
        return np.nan
    e = a - a.mean()
    n = len(a)
    var = float(np.sum(e * e) / n)
    for lag in range(1, min(lags, n - 1) + 1):
        cov = float(np.sum(e[lag:] * e[:-lag]) / n)
        var += 2 * (1 - lag / (lags + 1)) * cov
    return float(a.mean() / np.sqrt(max(var, 1e-15) / n))


def summarize(outcome: Outcome, calendar: pd.Index) -> dict:
    pnl = outcome.pnl.reindex(calendar, fill_value=0.0)
    active = outcome.active.reindex(calendar, fill_value=False)
    sd = pnl.std(ddof=1)
    t = float(pnl.mean() / (sd / np.sqrt(len(pnl)))) if sd > 0 else np.nan
    # With at least ~1,400 IS sessions, the normal tail is indistinguishable
    # at report precision and avoids adding an unpinned SciPy dependency.
    p = float(0.5 * erfc(t / sqrt(2))) if np.isfinite(t) else np.nan
    yearly = pnl.groupby(pnl.index.str[:4]).sum()
    equity = pnl.cumsum()
    running_peak = np.maximum.accumulate(
        np.concatenate(([0.0], equity.to_numpy(float)))
    )[1:]
    dd = equity.to_numpy(float) - running_peak
    return {
        "sessions": len(pnl),
        "active_days": int(active.sum()),
        "frequency": float(active.mean()),
        "mean_daily": float(pnl.mean()),
        "total": float(pnl.sum()),
        "t": t,
        "hac5_t": hac_t(pnl),
        "p_one_sided": p,
        "win_day": float((pnl[active] > 0).mean()) if active.any() else np.nan,
        "positive_years": int((yearly > 0).sum()),
        "year_count": len(yearly),
        "max_drawdown": float(dd.min()),
        "yearly": "|".join(f"{y}:{v:.2f}" for y, v in yearly.items()),
    }


def bh_qvalues(pvalues: pd.Series) -> pd.Series:
    p = pvalues.fillna(1.0).to_numpy(float)
    order = np.argsort(p)
    ranked = p[order]
    q = ranked * len(p) / np.arange(1, len(p) + 1)
    q = np.minimum.accumulate(q[::-1])[::-1].clip(max=1)
    out = np.empty_like(q)
    out[order] = q
    return pd.Series(out, index=pvalues.index)


def prepare_panel() -> pd.DataFrame:
    p = pd.read_parquet(DATA / "signal_panel.parquet")
    # One daily state value repeated across names.
    daily_disp = (
        p[["day", "disp5"]].drop_duplicates("day").sort_values("day")
    )
    daily_disp["disp5_ref"] = daily_disp["disp5"].rolling(
        60, min_periods=30
    ).median().shift(1)
    p = p.merge(daily_disp[["day", "disp5_ref"]], on="day", how="left")
    return p


def main() -> None:
    cells = manifest()
    cells.to_csv(BASE / "variant_manifest.csv", index=False)
    print(f"manifest frozen: {len(cells)} distinct cells", flush=True)

    panel = prepare_panel()
    is_panel = panel[panel["day"] <= IS_END]
    is_calendar = pd.Index(sorted(is_panel["day"].unique()), name="day")
    summaries = []
    is_daily = {}
    for i, row in cells.iterrows():
        outcome = evaluate_cell(is_panel, row)
        s = summarize(outcome, is_calendar)
        summaries.append({"cell_id": row["cell_id"], **s})
        is_daily[row["cell_id"]] = outcome.pnl.reindex(
            is_calendar, fill_value=0.0
        )
        if (i + 1) % 30 == 0:
            print(f"IS {i + 1}/{len(cells)}", flush=True)

    results = cells.merge(pd.DataFrame(summaries), on="cell_id")
    results["q_bh_180"] = bh_qvalues(results["p_one_sided"])
    results["is_pass"] = (
        (results["t"] >= 2.5)
        & (results["mean_daily"] >= 8.0)
        & (results["positive_years"] >= 4)
        & (results["frequency"] >= 0.40)
    )
    results.to_csv(BASE / "variant_is_results.csv", index=False)
    survivors = results[results["is_pass"]].copy()
    print(f"IS survivors: {len(survivors)}", flush=True)

    if survivors.empty:
        pd.DataFrame().to_csv(BASE / "variant_holdout_results.csv", index=False)
        return

    pd.DataFrame(is_daily)[survivors["cell_id"]].to_csv(
        BASE / "survivor_is_daily.csv"
    )
    hold = panel[panel["day"] >= HOLDOUT_START]
    hold_calendar = pd.Index(sorted(hold["day"].unique()), name="day")
    hold_rows = []
    hold_daily = {}
    for _, srow in survivors.iterrows():
        row = srow[cells.columns]
        normal = evaluate_cell(hold, row, 1.0)
        stressed = evaluate_cell(hold, row, 2.0)
        normal_stats = summarize(normal, hold_calendar)
        stressed_stats = summarize(stressed, hold_calendar)
        years = normal.pnl.reindex(hold_calendar, fill_value=0).groupby(
            hold_calendar.str[:4]
        ).sum()
        record = {
            "cell_id": row["cell_id"],
            **{f"hold_{k}": v for k, v in normal_stats.items()},
            "hold_2x_mean_daily": stressed_stats["mean_daily"],
            "hold_2x_total": stressed_stats["total"],
            "all_holdout_years_positive": bool((years > 0).all()),
        }
        record["hold_pass"] = bool(
            record["hold_mean_daily"] > 0
            and record["hold_t"] >= 1.5
            and record["hold_2x_mean_daily"] > 0
            and record["all_holdout_years_positive"]
        )
        hold_rows.append(record)
        hold_daily[row["cell_id"]] = normal.pnl.reindex(
            hold_calendar, fill_value=0.0
        )
    hold_results = survivors.merge(
        pd.DataFrame(hold_rows), on="cell_id", suffixes=("", "_hold")
    )
    hold_results.to_csv(BASE / "variant_holdout_results.csv", index=False)
    pd.DataFrame(hold_daily).to_csv(BASE / "survivor_holdout_daily.csv")
    print(
        f"holdout survivors: {int(hold_results['hold_pass'].sum())}/"
        f"{len(hold_results)}",
        flush=True,
    )


if __name__ == "__main__":
    main()
