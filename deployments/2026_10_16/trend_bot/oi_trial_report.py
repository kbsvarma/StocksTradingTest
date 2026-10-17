"""Scoreboard for forward trial #2 — OI divergence (docs/PREREG_OI_FORWARD.md).
Frozen: falling = 5-trading-day front-month OI pct change < 0 (roll-masked).
Verdict: forward falling-OI entries mean > rising-OI mean, Welch t>=2,
minimum 50 falling-OI closed trades. Run: python oi_trial_report.py"""
from pathlib import Path
import pandas as pd

from trial_stats import normal_approx_two_sided_p, welch_t

SDIR = Path(__file__).parent / "state"
TRIAL_START = pd.Timestamp("2026-07-06", tz="UTC")

def main():
    dec = pd.read_csv(SDIR / "decisions.csv")
    tp = SDIR / "trades.csv"
    trades = pd.read_csv(tp) if tp.exists() else pd.DataFrame()
    if "oi_state" not in dec.columns or not len(trades):
        print("trial armed — no stamped closed trades yet")
        return
    dec["ts"] = pd.to_datetime(dec["ts"])
    trades["entry_time"] = pd.to_datetime(trades["entry_time"])
    trades = trades[trades["entry_time"] >= TRIAL_START]
    de = dec[dec["acted"] == True].copy()                    # noqa: E712
    de["key"] = de["sym"] + de["ts"].dt.floor("h").astype(str)
    trades["key"] = trades["sym"] + trades["entry_time"].dt.floor("h").astype(str)
    m = trades.merge(de[["key", "oi_state"]], on="key", how="left").dropna(subset=["oi_state"])
    f = m[m["oi_state"] == "falling"]["pnl_usd"]
    r = m[m["oi_state"] == "rising"]["pnl_usd"]
    print(f"forward since {TRIAL_START.date()}: falling n={len(f)} mean "
          f"${f.mean() if len(f) else float('nan'):.2f} | rising n={len(r)} "
          f"mean ${r.mean() if len(r) else float('nan'):.2f}")
    if len(f) < 50:
        print(f"STATUS: accumulating — {len(f)}/50 falling-OI trades. No read.")
        return
    t = welch_t(f, r)
    p_approx = normal_approx_two_sided_p(t)
    print(f"welch t={t:.2f} normal-approx-p={p_approx:.3f} -> "
          f"{'CONFIRMED' if f.mean() > r.mean() and t >= 2 else 'NOT confirmed (yet)'}")

if __name__ == "__main__":
    main()
