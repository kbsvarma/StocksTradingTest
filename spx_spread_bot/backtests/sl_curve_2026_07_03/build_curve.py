"""Build combined_curve.csv + skip accounting from the 8 completed sweep runs."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

BT = Path(__file__).resolve().parent.parent
OUT = Path(__file__).resolve().parent

RUNS = {
    1.00: "stop_100",
    1.25: "stop_125",
    1.50: "stop_150",
    1.75: "stop_175",
    2.00: "stop_200",
    2.50: "stop_250",
    3.00: "stop_300",
    99.0: "stop_nosl",
}


def main():
    rows = []
    skip_rows = []
    entered_sets = {}
    for mult, rel in RUNS.items():
        d = BT / rel
        daily = pd.read_csv(d / "daily_results.csv")
        trades = pd.read_csv(d / "trade_results.csv")
        pnl = pd.to_numeric(trades["Total PnL"], errors="coerce").fillna(0.0)
        eq = pnl.cumsum()
        dd = eq - eq.cummax()
        yearly = trades.assign(year=trades["Date"].str[:4]).groupby("year")["Total PnL"].sum().round(0)
        wins = int((pnl >= 0).sum())
        row = {
            "multiplier": mult,
            "label": "no-SL(99x)" if mult == 99.0 else f"{mult:.2f}x",
            "n_days_evaluated": len(daily),
            "n_trades": len(trades),
            "total_pnl": round(float(pnl.sum()), 0),
            "avg_pnl": round(float(pnl.mean()), 2),
            "win_rate": round(wins / len(trades), 4),
            "max_dd": round(float(dd.min()), 0),
            "worst_day": round(float(pnl.min()), 0),
            "best_day": round(float(pnl.max()), 0),
            "std_per_trade": round(float(pnl.std()), 1),
            "sharpe_like": round(float(pnl.mean() / (pnl.std() + 1e-9)), 3),
            "sl_exit_count": int((trades["Exit reason"] == "stop_loss").sum()),
            "pt_exit_count": int((trades["Exit reason"] == "profit_target").sum()),
            "expiry_count": int((trades["Exit reason"] == "expiry").sum()),
            "eod_close_count": int((trades["Exit reason"] == "eod_force_close").sum()),
            "friday_close_count": int((trades["Exit reason"] == "friday_force_close").sum()),
        }
        for y, v in yearly.items():
            row[f"pnl_{y}"] = v
        rows.append(row)
        entered_sets[mult] = set(trades["Date"])

        sk = daily[daily["decision"] != "ENTER"]["reason"].fillna("unknown")
        sk = sk.map(lambda r: "VIX above maximum" if str(r).startswith("VIX above maximum") else r)
        sk = sk.map(lambda r: "error: cache-miss" if str(r).startswith("error: cache-miss") else r)
        for reason, cnt in sk.value_counts().items():
            skip_rows.append({"multiplier": mult, "reason": reason, "count": int(cnt)})

    curve = pd.DataFrame(rows).sort_values("multiplier")
    tmp = OUT / "combined_curve.csv.tmp"
    curve.to_csv(tmp, index=False)
    tmp.replace(OUT / "combined_curve.csv")

    skips = pd.DataFrame(skip_rows).pivot_table(index="reason", columns="multiplier", values="count", fill_value=0).astype(int)
    tmp = OUT / "skip_accounting.csv.tmp"
    skips.to_csv(tmp)
    tmp.replace(OUT / "skip_accounting.csv")

    # identical-day-set verification across multipliers
    base = entered_sets[1.00]
    diffs = {m: (sorted(s - base), sorted(base - s)) for m, s in entered_sets.items() if s != base}
    print("entered-day-set identical across multipliers:", not diffs)
    if diffs:
        print(json.dumps({str(k): v for k, v in diffs.items()}, indent=1)[:2000])

    print(curve.drop(columns=[c for c in curve.columns if c.startswith("pnl_")]).to_string(index=False))
    print()
    print(curve[["label"] + [c for c in curve.columns if c.startswith("pnl_")]].to_string(index=False))
    print()
    print(skips.to_string())


if __name__ == "__main__":
    main()
