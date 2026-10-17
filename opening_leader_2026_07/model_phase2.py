"""Walk-forward ridge aggregation of causal first-hour features."""
from __future__ import annotations

from math import erfc, sqrt
from pathlib import Path

import numpy as np
import pandas as pd

import variant_search as vs

BASE = Path(__file__).resolve().parent
DATA = BASE / "data"
ALPHAS = (0.01, 0.1, 1.0)
FAMILIES = ("price", "price_volume", "full")
HORIZONS = ("5", "10", "15")
PORTFOLIOS = ("long1", "short1", "neutral1", "neutral3", "neutral5")
FOLDS = (
    ("2020-12-31", "2021-01-01", "2021-12-31"),
    ("2021-12-31", "2022-01-01", "2022-12-31"),
    ("2022-12-31", "2023-01-01", "2023-12-31"),
)


def feature_frame(p: pd.DataFrame, horizon: str, family: str) -> tuple[pd.DataFrame, list[str]]:
    x = p.copy()
    close_col = {"5": "c0934", "10": "c0939", "15": "c0944"}[horizon]
    ret_horizons = [h for h in ("5", "10", "15") if int(h) <= int(horizon)]

    for h in ret_horizons:
        x[f"f_{h}_rank"] = x[f"r{h}_rank"] - 0.5
        x[f"f_{h}_abs"] = x[f"r{h}"].abs().clip(upper=0.15)
        x[f"f_{h}_squash"] = np.tanh(x[f"r{h}"] / 0.02)

    width = (x[f"hi{horizon}"] - x[f"lo{horizon}"]) / x["o0930"]
    x["f_range"] = width.clip(0, 0.20)
    x["f_location"] = (
        (x[close_col] - x[f"lo{horizon}"])
        / (x[f"hi{horizon}"] - x[f"lo{horizon}"]).replace(0, np.nan)
        - 0.5
    ).clip(-0.5, 0.5)
    x["f_log_dv"] = np.log(x["med_dv"].clip(lower=1.0))
    x["f_mega"] = x["mega"].astype(float)

    features = []
    for h in ret_horizons:
        features.extend([f"r{h}", f"f_{h}_rank", f"f_{h}_abs", f"f_{h}_squash"])
    features.extend(["f_range", "f_location", "f_log_dv", "f_mega"])

    if family in ("price_volume", "full"):
        x["f_rvol"] = x[f"log_rvol{horizon}"].clip(-4, 4)
        x["f_rvol_rank"] = x[f"rvol{horizon}_rank"] - 0.5
        x["f_ret_x_rvol"] = x[f"r{horizon}"].clip(-0.15, 0.15) * x["f_rvol"]
        x["f_rank_x_rvol"] = x[f"f_{horizon}_rank"] * x["f_rvol_rank"]
        features.extend(
            ["f_rvol", "f_rvol_rank", "f_ret_x_rvol", "f_rank_x_rvol"]
        )

    if family == "full":
        x["f_gap"] = x["gap_utc"].clip(-0.30, 0.30)
        x["f_gap_rank"] = x["gap_rank"] - 0.5
        x["f_prev"] = x["prev_day_ret"].clip(-0.30, 0.30)
        x["f_breadth_x_ret"] = (
            (x[f"breadth{horizon}"] - 0.5) * x[f"r{horizon}"].clip(-0.15, 0.15)
        )
        x["f_disp_x_ret"] = (
            x[f"disp{horizon}"].clip(0, 0.10)
            * x[f"r{horizon}"].clip(-0.15, 0.15)
        )
        x["f_gap_x_ret"] = x["f_gap"] * x[f"r{horizon}"].clip(-0.15, 0.15)
        features.extend(
            [
                "f_gap", "f_gap_rank", "f_prev", "f_breadth_x_ret",
                "f_disp_x_ret", "f_gap_x_ret",
            ]
        )

    entry = vs.ENTRY[horizon]
    x["target"] = x["x1024"] / x[entry] - 1
    keep = [
        "day", "sym", "mega", entry, "x1024", "target", *features,
    ]
    x = x[keep].replace([np.inf, -np.inf], np.nan).dropna()
    x = x[(x[entry] > 0) & (x["x1024"] > 0)]
    return x, features


def weighted_ridge(
    train: pd.DataFrame,
    test: pd.DataFrame,
    features: list[str],
    alpha: float,
) -> np.ndarray:
    """Fit a day-equal weighted, standardized ridge and predict test rows."""
    x0 = train[features].to_numpy(float)
    xt = test[features].to_numpy(float)
    y = train["target"].clip(-0.10, 0.10).to_numpy(float)
    day_n = train.groupby("day")["sym"].transform("size").to_numpy(float)
    w = 1.0 / day_n
    w /= w.sum()

    mean = np.sum(x0 * w[:, None], axis=0)
    var = np.sum((x0 - mean) ** 2 * w[:, None], axis=0)
    scale = np.sqrt(np.maximum(var, 1e-12))
    x0 = (x0 - mean) / scale
    xt = (xt - mean) / scale
    design = np.column_stack([np.ones(len(x0)), x0])
    test_design = np.column_stack([np.ones(len(xt)), xt])
    penalty = np.eye(design.shape[1]) * alpha
    penalty[0, 0] = 0.0
    # np.matmul emits spurious overflow warnings with Apple's Accelerate on
    # these finite float64 arrays; explicit einsum is quiet and equivalent.
    gram = np.einsum("ni,n,nj->ij", design, w, design) + penalty
    rhs = np.einsum("ni,n,n->i", design, w, y)
    beta = np.linalg.solve(gram, rhs)
    return np.einsum("ij,j->i", test_design, beta)


def predictions_oof(
    panel: pd.DataFrame, horizon: str, family: str, alpha: float
) -> pd.DataFrame:
    x, features = feature_frame(panel, horizon, family)
    pieces = []
    for train_end, test_start, test_end in FOLDS:
        train = x[x["day"] <= train_end]
        test = x[(x["day"] >= test_start) & (x["day"] <= test_end)].copy()
        test["pred"] = weighted_ridge(train, test, features, alpha)
        pieces.append(test)
    return pd.concat(pieces, ignore_index=True)


def prediction_final(
    panel: pd.DataFrame, horizon: str, family: str, alpha: float
) -> pd.DataFrame:
    x, features = feature_frame(panel, horizon, family)
    train = x[x["day"] <= "2023-12-31"]
    test = x[x["day"] >= "2024-01-01"].copy()
    test["pred"] = weighted_ridge(train, test, features, alpha)
    return test


def portfolio_outcome(
    pred: pd.DataFrame,
    horizon: str,
    portfolio: str,
    spread_mult: float = 1.0,
) -> vs.Outcome:
    ordered = pred.sort_values(["day", "pred", "sym"])
    entry = vs.ENTRY[horizon]
    if portfolio == "long1":
        top = ordered.groupby("day", sort=False).tail(1)
        return vs.leg_pnl(top, 1, entry, "x1024", 10_000, spread_mult)
    if portfolio == "short1":
        bottom = ordered.groupby("day", sort=False).head(1)
        return vs.leg_pnl(bottom, -1, entry, "x1024", 10_000, spread_mult)
    k = int(portfolio.replace("neutral", ""))
    bottom = ordered.groupby("day", sort=False).head(k)
    top = ordered.groupby("day", sort=False).tail(k)
    return vs.merge_outcomes(
        vs.leg_pnl(top, 1, entry, "x1024", 5_000, spread_mult),
        vs.leg_pnl(bottom, -1, entry, "x1024", 5_000, spread_mult),
    )


def manifest() -> pd.DataFrame:
    rows = []
    i = 0
    for family in FAMILIES:
        for horizon in HORIZONS:
            for alpha in ALPHAS:
                for portfolio in PORTFOLIOS:
                    i += 1
                    rows.append(
                        {
                            "cell_id": f"M_{i:03d}", "family": family,
                            "horizon": horizon, "alpha": alpha,
                            "portfolio": portfolio,
                        }
                    )
    out = pd.DataFrame(rows)
    assert len(out) == 135
    return out


def bh(pvalues: pd.Series) -> pd.Series:
    p = pvalues.fillna(1).to_numpy(float)
    order = np.argsort(p)
    ranked = p[order]
    q = np.minimum.accumulate(
        (ranked * len(p) / np.arange(1, len(p) + 1))[::-1]
    )[::-1].clip(max=1)
    result = np.empty_like(q)
    result[order] = q
    return pd.Series(result, index=pvalues.index)


def main() -> None:
    cells = manifest()
    cells.to_csv(BASE / "model_manifest.csv", index=False)
    print("model manifest frozen: 135 cells", flush=True)
    panel = vs.prepare_panel()
    calendar = pd.Index(
        sorted(
            panel.loc[
                (panel["day"] >= "2021-01-01")
                & (panel["day"] <= "2023-12-31"),
                "day",
            ].unique()
        ),
        name="day",
    )

    rows = []
    predictions: dict[tuple[str, str, float], pd.DataFrame] = {}
    for family in FAMILIES:
        for horizon in HORIZONS:
            for alpha in ALPHAS:
                key = (family, horizon, alpha)
                pred = predictions_oof(panel, horizon, family, alpha)
                predictions[key] = pred
                for portfolio in PORTFOLIOS:
                    cell = cells[
                        (cells["family"] == family)
                        & (cells["horizon"] == horizon)
                        & (cells["alpha"] == alpha)
                        & (cells["portfolio"] == portfolio)
                    ].iloc[0]
                    outcome = portfolio_outcome(pred, horizon, portfolio)
                    summary = vs.summarize(outcome, calendar)
                    rows.append({"cell_id": cell["cell_id"], **summary})
                print(f"OOF {family}/{horizon}/a={alpha}", flush=True)

    results = cells.merge(pd.DataFrame(rows), on="cell_id")
    results["q_bh_135"] = bh(results["p_one_sided"])
    results["oof_pass"] = (
        (results["mean_daily"] >= 5)
        & (results["t"] >= 2.5)
        & (results["q_bh_135"] <= 0.10)
        & (results["positive_years"] == 3)
        & (results["frequency"] >= 0.95)
    )
    results.to_csv(BASE / "model_oof_results.csv", index=False)
    passed = results[results["oof_pass"]].copy()
    print(f"OOF passers: {len(passed)}", flush=True)
    if passed.empty:
        pd.DataFrame().to_csv(BASE / "model_holdout_result.csv", index=False)
        return

    complexity = {"price": 0, "price_volume": 1, "full": 2}
    passed["complexity"] = passed["family"].map(complexity)
    winner = passed.sort_values(
        ["hac5_t", "complexity", "alpha"],
        ascending=[False, True, False],
    ).iloc[0]
    pred = prediction_final(
        panel, winner["horizon"], winner["family"], float(winner["alpha"])
    )
    normal = portfolio_outcome(
        pred, winner["horizon"], winner["portfolio"], 1.0
    )
    stressed = portfolio_outcome(
        pred, winner["horizon"], winner["portfolio"], 2.0
    )
    hold_calendar = pd.Index(
        sorted(panel.loc[panel["day"] >= "2024-01-01", "day"].unique()),
        name="day",
    )
    ns = vs.summarize(normal, hold_calendar)
    ss = vs.summarize(stressed, hold_calendar)
    yearly = normal.pnl.reindex(hold_calendar, fill_value=0).groupby(
        hold_calendar.str[:4]
    ).sum()
    record = {
        **winner[cells.columns].to_dict(),
        **{f"hold_{k}": v for k, v in ns.items()},
        "hold_2x_mean_daily": ss["mean_daily"],
        "hold_2x_total": ss["total"],
        "all_holdout_years_positive": bool((yearly > 0).all()),
    }
    record["hold_pass"] = bool(
        record["hold_t"] >= 1.5
        and record["hold_2x_mean_daily"] > 0
        and record["all_holdout_years_positive"]
    )
    pd.DataFrame([record]).to_csv(BASE / "model_holdout_result.csv", index=False)
    normal.pnl.reindex(hold_calendar, fill_value=0).to_csv(
        BASE / "model_holdout_daily.csv", header=["pnl"]
    )
    print(
        f"holdout {winner['cell_id']}: pass={record['hold_pass']} "
        f"mean={record['hold_mean_daily']:.2f} t={record['hold_t']:.2f}",
        flush=True,
    )


if __name__ == "__main__":
    main()
