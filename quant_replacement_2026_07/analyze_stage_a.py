"""Registered 2011-20 volatility normalization for Stage-A daily paths."""
from pathlib import Path

import pandas as pd


HERE = Path(__file__).resolve().parent


def drawdown(pnl: pd.Series) -> float:
    equity = pnl.cumsum()
    return float((equity - equity.cummax().clip(lower=0.0)).min())


def run():
    pnl = pd.read_parquet(HERE / "stage_a_daily_pnl.parquet")
    train = pnl[pnl.index.year <= 2020]
    test = pnl[pnl.index.year >= 2021]
    target = float(train["donchian20"].std(ddof=1))
    rows = []
    for candidate in pnl:
        candidate_vol = float(train[candidate].std(ddof=1))
        scale = target / candidate_vol if candidate_vol > 0 else float("nan")
        scaled_train, scaled_test = train[candidate] * scale, test[candidate] * scale
        scaled_full = pnl[candidate] * scale
        rows.append({
            "candidate": candidate, "scale": scale,
            "train_net": float(scaled_train.sum()),
            "test_net": float(scaled_test.sum()),
            "full_net": float(scaled_full.sum()),
            "train_dd": drawdown(scaled_train),
            "test_dd": drawdown(scaled_test),
            "full_dd": drawdown(scaled_full),
        })
    result = pd.DataFrame(rows).sort_values("test_net", ascending=False)
    result.to_csv(HERE / "stage_a_risk_normalized.csv", index=False)
    print(result.to_string(index=False,
          float_format=lambda value: f"{value:,.3f}"))
    return result


if __name__ == "__main__":
    run()
