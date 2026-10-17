"""Scoreboard for the pre-registered vol-regime forward trial.

Reads the bot's own decisions.csv + trades.csv (forward data only, from
2026-07-06) and scores the frozen hypothesis: do "energetic"-regime entries
(VIX > 20.72 at signal) outperform the rest?

Run any time:  python regime_trial_report.py
Verdict rules (frozen in docs/PREREG_REGIME_FORWARD.md):
  - no read before 50 energetic-entry closed trades
  - CONFIRMED when energetic mean > rest mean with NW t >= 2
  - 12-month directional checkpoint (informational only)
"""
from pathlib import Path

import pandas as pd

from trial_stats import newey_west_indicator_t, normal_approx_two_sided_p

SDIR = Path(__file__).parent / "state"
TRIAL_START = pd.Timestamp("2026-07-06", tz="UTC")
VIX_ENERGETIC = 20.72          # frozen 2026-07-06 — never recompute

def main():
    dec = pd.read_csv(SDIR / "decisions.csv")
    tr_p = SDIR / "trades.csv"
    trades = pd.read_csv(tr_p) if tr_p.exists() else pd.DataFrame()
    if not len(trades):
        print("no closed trades yet — trial is armed, ledger empty")
        return
    dec["ts"] = pd.to_datetime(dec["ts"])
    trades["entry_time"] = pd.to_datetime(trades["entry_time"])
    trades["exit_time"] = pd.to_datetime(trades["exit_time"])
    trades = trades[trades["entry_time"] >= TRIAL_START]
    if "vix" not in dec.columns:
        print("no vix column yet (pre-instrumentation decisions only)")
        return
    # match each trade to the decision row that opened it (same sym,
    # decision ts within the entry hour)
    dec_e = dec[dec["acted"] == True].copy()                      # noqa: E712
    dec_e["key"] = dec_e["sym"] + dec_e["ts"].dt.floor("h").astype(str)
    trades["key"] = trades["sym"] + trades["entry_time"].dt.floor("h").astype(str)
    m = trades.merge(dec_e[["key", "vix"]], on="key", how="left")
    m = m.dropna(subset=["vix"])
    m["energetic"] = m["vix"] > VIX_ENERGETIC

    e = m[m["energetic"]]["pnl_usd"]
    r = m[~m["energetic"]]["pnl_usd"]
    print(f"forward trial since {TRIAL_START.date()} — closed trades with "
          f"regime stamp: {len(m)}")
    print(f"  energetic (VIX>{VIX_ENERGETIC}): n={len(e):>4}  "
          f"mean ${e.mean() if len(e) else float('nan'):>8.2f}")
    print(f"  rest:                      n={len(r):>4}  "
          f"mean ${r.mean() if len(r) else float('nan'):>8.2f}")
    if len(e) < 50:
        print(f"\nSTATUS: accumulating — {len(e)}/50 energetic trades before "
              "any read. No conclusions drawn.")
        return
    diff = e.mean() - r.mean()
    ordered = m.sort_values("exit_time")
    t_stat = newey_west_indicator_t(
        ordered["pnl_usd"], ordered["energetic"].astype(int), lags=3)
    p_approx = normal_approx_two_sided_p(t_stat)
    verdict = ("CONFIRMED" if diff > 0 and t_stat >= 2
               else "NOT confirmed (yet)")
    print(f"\n  diff ${diff:+.2f}/trade   NW-t={t_stat:.2f}  "
          f"normal-approx-p={p_approx:.3f}")
    print(f"STATUS: {verdict}")


if __name__ == "__main__":
    main()
