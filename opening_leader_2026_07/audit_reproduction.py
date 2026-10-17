"""Independent reproduction and integrity audit of the opening-leader report."""
from pathlib import Path

import numpy as np
import pandas as pd

BASE = Path(__file__).resolve().parent
DATA = BASE / "data"
DEGRADED_DAYS = {"2021-07-07", "2021-10-26", "2022-09-19"}


def pick_primary(panel: pd.DataFrame, top_n: int = 150) -> pd.DataFrame:
    """Recreate the selection without importing or calling engine.py."""
    x = panel
    if top_n < 150:
        monthly = (
            panel[["month", "sym", "med_dv"]]
            .drop_duplicates(["month", "sym"])
            .sort_values(["month", "med_dv"], ascending=[True, False])
        )
        allowed = monthly.groupby("month", sort=False).head(top_n)
        x = panel.merge(
            allowed[["month", "sym"]],
            on=["month", "sym"],
            how="inner",
            validate="many_to_one",
        )
    x = x.dropna(
        subset=["o0930", "c0934", "prev_utc_close", "gap_utc", "r5"]
    ).copy()
    x = x[(x["gap_utc"] > 0) & (x["r5"] > 0)]
    # Subtracting a common daily median cannot alter argmax; the original
    # "market-relative" pick is exactly the raw-r5 maximum.
    x = x.sort_values(["day", "r5", "sym"], ascending=[True, False, True])
    return x.drop_duplicates("day", keep="first")


def simulate_paths(
    picks: pd.DataFrame,
    entry_hm: str = "09:36",
    exit_hm: str = "10:25",
    use_stop: bool = True,
) -> pd.DataFrame:
    """Simulate one declared rule directly from the raw minute bars."""
    results = []
    pick_cols = ["day", "sym", "month", "mega", "gap_utc", "r5", "lo5"]
    wanted = picks[pick_cols].copy()
    for year, ypicks in wanted.groupby(wanted["day"].str[:4]):
        raw = pd.read_parquet(DATA / f"minutes_{year}.parquet")
        ids = pd.read_parquet(DATA / f"idmap_{year}.parquet")[
            ["day", "instrument_id", "raw_symbol"]
        ]
        raw = raw.merge(
            ids, on=["day", "instrument_id"], how="left", validate="many_to_one"
        ).rename(columns={"raw_symbol": "sym"})
        raw = raw.merge(
            ypicks, on=["day", "sym"], how="inner", validate="many_to_one"
        )
        raw["hm"] = (
            pd.to_datetime(raw["ts_event"], utc=True)
            .dt.tz_convert("America/New_York")
            .dt.strftime("%H:%M")
        )
        raw = raw[(raw["hm"] >= "09:30") & (raw["hm"] <= "10:30")]

        for (day, sym), bars0 in raw.groupby(["day", "sym"], sort=True):
            bars = bars0.sort_values("ts_event").set_index("hm")
            if entry_hm not in bars.index:
                continue
            meta = bars.iloc[0]
            entry = float(bars.at[entry_hm, "open"])
            stop = float(meta["lo5"])
            walk = bars.loc[entry_hm:exit_hm]
            exit_px = None
            reason = "time"
            if use_stop:
                for _, bar in walk.iterrows():
                    if float(bar["low"]) <= stop:
                        exit_px = min(stop, float(bar["open"]))
                        reason = "stop"
                        break
            if exit_px is None:
                if exit_hm in bars.index:
                    exit_px = float(bars.at[exit_hm, "close"])
                else:
                    exit_px = float(walk["close"].iloc[-1])
                    reason = "last_bar"

            hs = 0.0003 if bool(meta["mega"]) else 0.0005
            buy = entry * (1 + hs)
            sell = exit_px * (1 - hs)
            shares = 10_000.0 / buy
            net = shares * (sell - buy) - 2 * shares * 0.005
            buy2 = entry * (1 + 2 * hs)
            sell2 = exit_px * (1 - 2 * hs)
            shares2 = 10_000.0 / buy2
            net2 = shares2 * (sell2 - buy2) - 2 * shares2 * 0.005
            results.append(
                {
                    "day": day,
                    "sym": sym,
                    "entry": entry,
                    "exit": exit_px,
                    "reason": reason,
                    "gross": exit_px / entry - 1,
                    "net_usd": net,
                    "net2x_usd": net2,
                    "gap": float(meta["gap_utc"]),
                    "r5": float(meta["r5"]),
                }
            )
    return pd.DataFrame(results).sort_values("day").reset_index(drop=True)


def naive_t(x: pd.Series) -> float:
    return float(x.mean() / (x.std(ddof=1) / np.sqrt(len(x))))


def hac_t(x: pd.Series, lags: int = 5) -> float:
    """Newey-West t-stat for the sample mean."""
    a = x.to_numpy(dtype=float)
    n = len(a)
    e = a - a.mean()
    # Elementwise sums avoid a noisy Accelerate/BLAS warning seen on this Mac
    # for otherwise ordinary finite float64 dot products.
    long_var = float(np.sum(e * e) / n)
    for lag in range(1, min(lags, n - 1) + 1):
        gamma = float(np.sum(e[lag:] * e[:-lag]) / n)
        long_var += 2 * (1 - lag / (lags + 1)) * gamma
    return float(a.mean() / np.sqrt(long_var / n))


def moving_block_ci(
    x: pd.Series, block: int = 20, draws: int = 10_000
) -> tuple[float, float]:
    """Deterministic 95% moving-block bootstrap CI for mean dollars/trade."""
    a = x.to_numpy(dtype=float)
    n = len(a)
    rng = np.random.default_rng(20260731)
    starts = np.arange(0, n - block + 1)
    means = np.empty(draws)
    blocks_needed = int(np.ceil(n / block))
    for i in range(draws):
        chosen = rng.choice(starts, blocks_needed, replace=True)
        sample = np.concatenate([a[s : s + block] for s in chosen])[:n]
        means[i] = sample.mean()
    lo, hi = np.quantile(means, [0.025, 0.975])
    return float(lo), float(hi)


def main() -> None:
    panel = pd.read_parquet(DATA / "signal_panel.parquet")
    picks = pick_primary(panel)
    reproduced = simulate_paths(picks)
    s1 = simulate_paths(picks, "09:40", "10:30", True)
    s5 = simulate_paths(picks, "09:36", "10:25", False)
    s2 = simulate_paths(pick_primary(panel, top_n=100))
    saved = pd.read_csv(BASE / "trades_primary.csv", dtype={"day": str})

    merged = reproduced.merge(
        saved,
        on=["day", "sym"],
        suffixes=("_audit", "_saved"),
        how="outer",
        indicator=True,
    )
    common = merged["_merge"] == "both"
    max_net_diff = float(
        (
            merged.loc[common, "net_usd_audit"]
            - merged.loc[common, "net_usd_saved"]
        )
        .abs()
        .max()
    )

    headline = reproduced[reproduced["day"] < "2026-05-01"].copy()
    s1_headline = s1[s1["day"] < "2026-05-01"].copy()
    s5_headline = s5[s5["day"] < "2026-05-01"].copy()
    s2_headline = s2[s2["day"] < "2026-05-01"].copy()
    generation = reproduced[reproduced["day"] >= "2026-05-01"].copy()
    exact_generation = reproduced[
        (reproduced["day"] >= "2026-05-21")
        & (reproduced["day"] <= "2026-07-02")
    ].copy()
    clean = headline[~headline["day"].isin(DEGRADED_DAYS)]
    ci = moving_block_ci(headline["net_usd"])

    expected_name_days = panel["day"].nunique() * 150
    endpoint_valid = int(
        (panel["o0930"].notna() & panel["c0934"].notna()).sum()
    )
    audit_rows = [
        ("cached_sessions", int(panel["day"].nunique())),
        ("cached_last_day", panel["day"].max()),
        ("expected_universe_name_days", expected_name_days),
        ("usable_0930_0934_name_days", endpoint_valid),
        (
            "unusable_endpoint_fraction",
            (expected_name_days - endpoint_valid) / expected_name_days,
        ),
        ("degraded_days_in_logs", ",".join(sorted(DEGRADED_DAYS))),
        ("selected_splitlike_abs_gap_gt_30pct", int((reproduced["gap"].abs() > 0.30).sum())),
        ("reproduced_trades_all", len(reproduced)),
        ("saved_trades_all", len(saved)),
        ("rows_only_one_side", int((~common).sum())),
        ("max_abs_saved_net_difference", max_net_diff),
        ("headline_n", len(headline)),
        ("headline_mean_usd", float(headline["net_usd"].mean())),
        ("headline_total_usd", float(headline["net_usd"].sum())),
        ("headline_naive_t", naive_t(headline["net_usd"])),
        ("headline_hac5_t", hac_t(headline["net_usd"], 5)),
        ("headline_block_bootstrap_mean_ci_lo", ci[0]),
        ("headline_block_bootstrap_mean_ci_hi", ci[1]),
        ("headline_gross_bps", float(headline["gross"].mean() * 1e4)),
        ("headline_2x_mean_usd", float(headline["net2x_usd"].mean())),
        ("s1_n", len(s1_headline)),
        ("s1_mean_usd", float(s1_headline["net_usd"].mean())),
        ("s1_t", naive_t(s1_headline["net_usd"])),
        ("s5_n", len(s5_headline)),
        ("s5_mean_usd", float(s5_headline["net_usd"].mean())),
        ("s5_t", naive_t(s5_headline["net_usd"])),
        ("s2_top100_n", len(s2_headline)),
        ("s2_top100_mean_usd", float(s2_headline["net_usd"].mean())),
        ("s2_top100_t", naive_t(s2_headline["net_usd"])),
        ("clean_degraded_removed_mean_usd", float(clean["net_usd"].mean())),
        ("clean_degraded_removed_t", naive_t(clean["net_usd"])),
        ("generation_n", len(generation)),
        ("generation_mean_usd", float(generation["net_usd"].mean())),
        ("generation_t", naive_t(generation["net_usd"])),
        ("exact_mined_window_n", len(exact_generation)),
        ("exact_mined_window_mean_usd", float(exact_generation["net_usd"].mean())),
        ("exact_mined_window_t", naive_t(exact_generation["net_usd"])),
    ]
    audit = pd.DataFrame(audit_rows, columns=["check", "value"])
    audit.to_csv(BASE / "audit_results.csv", index=False)
    reproduced.to_csv(BASE / "trades_primary_reproduced.csv", index=False)
    print(audit.to_string(index=False))


if __name__ == "__main__":
    main()
