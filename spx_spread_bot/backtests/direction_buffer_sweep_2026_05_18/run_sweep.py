"""Direction-filter buffer sweep for SPX 0DTE BPS.

Reuses the already-simulated 412-trade dataset from entry_time_sweep_2026_05_18
(10:30 ET entry, 50pt wide, 0.75% OTM, 1 contract, SL=2.0x, VIX 12-25, min_credit
~$1.20). The baseline (buffer=0) is the current live filter: SPX >= open.

We test buffers in points applied to the ES-delta-from-open proxy
(es_at_T - es_open), which ~1:1 matches SPX delta from open at 10:30 ET.
"""
from __future__ import annotations
from pathlib import Path
import numpy as np
import pandas as pd

SRC = Path("/Users/varmakammili/Documents/GitHub/StocksTradingTest/spx_spread_bot/backtests/entry_time_sweep_2026_05_18/variant_1030.csv")
OUT = Path("/Users/varmakammili/Documents/GitHub/StocksTradingTest/spx_spread_bot/backtests/direction_buffer_sweep_2026_05_18")
OUT.mkdir(parents=True, exist_ok=True)

BUFFERS = [0, 2, 5, 10, 20]


def metrics(df: pd.DataFrame) -> dict:
    if df.empty:
        return {"trades": 0}
    pnl = df["net_pnl_$"]
    wins = df[df["win"]]
    losses = df[~df["win"]]
    cum = pnl.cumsum()
    sharpe = (pnl.mean() / pnl.std() * np.sqrt(252)) if pnl.std() else 0.0
    return {
        "trades": len(df),
        "win_rate": df["win"].mean(),
        "total_pnl": pnl.sum(),
        "avg_pnl": pnl.mean(),
        "median_pnl": pnl.median(),
        "avg_win": wins["net_pnl_$"].mean() if not wins.empty else 0.0,
        "avg_loss": losses["net_pnl_$"].mean() if not losses.empty else 0.0,
        "max_single_loss": pnl.min(),
        "max_single_win": pnl.max(),
        "max_dd": (cum.cummax() - cum).max(),
        "sharpe": sharpe,
    }


def main():
    df = pd.read_csv(SRC)
    df["es_delta"] = df["es_at_T"] - df["es_open"]
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").reset_index(drop=True)
    print(f"Baseline dataset: {len(df)} trades  total=${df['net_pnl_$'].sum():,.0f}  win_rate={df['win'].mean():.3f}")
    print()

    baseline_taken = df.copy()
    rows = []
    for b in BUFFERS:
        taken = df[df["es_delta"] >= b].copy()
        skipped = df[df["es_delta"] < b].copy()  # would have been taken at buffer 0 but blocked here

        m = metrics(taken)
        m["buffer_pt"] = b
        # Skipped-trade quality: of trades blocked vs baseline (buffer=0), how many were wins/losses?
        skipped_wins = int(skipped["win"].sum())
        skipped_losses = int((~skipped["win"]).sum())
        skipped_total_pnl = skipped["net_pnl_$"].sum()
        m["saves"] = skipped_losses  # blocked trades that lost money
        m["misses"] = skipped_wins   # blocked trades that would have won
        denom = skipped_wins + skipped_losses
        m["filter_quality"] = (skipped_losses / denom) if denom else float("nan")
        m["skipped_pnl_avoided"] = -skipped_total_pnl  # positive = good (avoided losses)
        rows.append(m)

        taken.to_csv(OUT / f"variant_{b:02d}pt.csv", index=False)
        print(
            f"buffer={b:>2}pt | trades={m['trades']:>3} | win={m['win_rate']:.3f} | "
            f"total=${m['total_pnl']:>8,.0f} | avg=${m['avg_pnl']:>7,.2f} | "
            f"avg_win=${m['avg_win']:>6,.2f} | avg_loss=${m['avg_loss']:>7,.2f} | "
            f"max_loss=${m['max_single_loss']:>7,.0f} | sharpe={m['sharpe']:.2f} | "
            f"saves={m['saves']:>2} misses={m['misses']:>2} qual={m['filter_quality']:.3f}"
        )

    summary = pd.DataFrame(rows)
    cols = [
        "buffer_pt", "trades", "win_rate", "total_pnl", "avg_pnl", "median_pnl",
        "avg_win", "avg_loss", "max_single_loss", "max_single_win",
        "max_dd", "sharpe", "saves", "misses", "filter_quality", "skipped_pnl_avoided",
    ]
    summary = summary[cols]
    summary.to_csv(OUT / "summary_table.csv", index=False)

    # Write report
    base = summary.iloc[0]
    lines = ["# Direction-Filter Buffer Sweep — 2026-05-18\n"]
    lines.append("## Setup\n")
    lines.append("- SPXW 0DTE BPS, 50pt wide, ~0.75% OTM, 1 contract")
    lines.append("- Entry @ 10:30 ET, exit @ 14:30 ET (matches live config)")
    lines.append("- SL = 2.0x credit (premium-based, current live setting)")
    lines.append("- min_credit ~$1.20, VIX gate 12-25")
    lines.append(f"- Dataset: 412 trades passing baseline filters, 2020-2026 (~5.5 yrs)")
    lines.append("- Buffer applied to `es_at_T - es_open` (ES 1-min proxy for SPX delta-from-open)\n")

    lines.append("## Results\n")
    lines.append(summary.to_markdown(index=False, floatfmt=",.3f"))
    lines.append("\n")

    lines.append("## Delta vs baseline (buffer=0)\n")
    delta_rows = []
    for _, r in summary.iterrows():
        delta_rows.append({
            "buffer_pt": int(r["buffer_pt"]),
            "trades_dropped": int(base["trades"] - r["trades"]),
            "total_pnl_delta": r["total_pnl"] - base["total_pnl"],
            "avg_pnl_delta": r["avg_pnl"] - base["avg_pnl"],
            "win_rate_delta": r["win_rate"] - base["win_rate"],
            "max_loss_improvement": r["max_single_loss"] - base["max_single_loss"],  # >0 = less bad
            "saves": int(r["saves"]),
            "misses": int(r["misses"]),
            "filter_quality": r["filter_quality"],
        })
    lines.append(pd.DataFrame(delta_rows).to_markdown(index=False, floatfmt=",.3f"))
    lines.append("\n")

    lines.append("## Today's anecdote (2026-05-18) — context\n")
    lines.append("SPX 7415.82 vs open 7415.07 = +0.75pt at scan. This would be blocked")
    lines.append("by any buffer >= 1pt. In the dataset, 5% of trades had es_delta <= 1.1pt.\n")

    (OUT / "REPORT.md").write_text("\n".join(lines))
    print()
    print(f"Wrote {OUT}/REPORT.md and per-variant CSVs.")


if __name__ == "__main__":
    main()
