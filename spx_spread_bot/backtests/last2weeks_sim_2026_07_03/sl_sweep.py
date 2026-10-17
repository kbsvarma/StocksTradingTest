"""SL-multiplier sweep on the SAME cached data. NO new Databento pull.

Reuses sim.py machinery. Entry set is identical across k (gates/credit independent of stop).
For each traded day and each k in {2.0, 2.5, 3.0, noSL}: does spread mark ever reach k*credit?
  yes -> STOP at that minute+mark (+$0.10 slip); no -> PM settlement.
"""
import csv
from datetime import datetime, time as dtime
from pathlib import Path
import pandas as pd

import sim  # reuse loaders + find_best_spread + spread_mid

HERE = Path(__file__).parent
OPT_DIR = HERE / "spxw_p_cbbo_1m"
OUT_CSV = HERE / "per_day_by_k.csv"

KS = [2.0, 2.5, 3.0, None]        # None = no stop-loss
KLABEL = {2.0: "2.0", 2.5: "2.5", 3.0: "3.0", None: "noSL"}
SLIP = 0.10
WIDTH = sim.WIDTH


def resolve_exit(legs, short_k, long_k, credit, day, spx_close, k):
    """Return (exit_reason, stop_minute, mark_or_settle, pnl_pts, pnl_usd)."""
    entry_ts = pd.Timestamp(datetime.combine(day, dtime(10, 30)), tz="America/New_York")
    end_ts = pd.Timestamp(datetime.combine(day, dtime(16, 0)), tz="America/New_York")
    minute_index = pd.date_range(start=entry_ts, end=end_ts, freq="1min")

    if k is not None:
        stop_price = round(credit * k, 2)
        for ts in minute_index:
            m = sim.spread_mid(legs, short_k, long_k, ts)
            if m is None:
                continue
            if 0 <= m <= WIDTH + 0.01 and m >= stop_price:
                pnl_pts = credit - (m + SLIP)
                return "STOP", ts.strftime("%H:%M"), round(m, 2), round(pnl_pts, 2), round(pnl_pts * 100, 2)

    # no stop hit (or noSL) -> PM settlement
    settle = max(0.0, short_k - spx_close) - max(0.0, long_k - spx_close)
    pnl_pts = credit - settle
    return "EXPIRED", "", round(settle, 2), round(pnl_pts, 2), round(pnl_pts * 100, 2)


def main():
    spx_df, vix_df = sim.load_spot()
    rows = []          # per (day,k)
    day_records = []   # to rebuild the trade set identically

    for ds in sim.DAYS:
        day = datetime.strptime(ds, "%Y%m%d").date()
        base = sim.simulate_day(ds, spx_df, vix_df)   # identical gates/entry logic
        traded = base["traded"] == "YES"
        short_k = float(base["short"]) if traded and base["short"] != "" else None
        long_k = float(base["long"]) if traded and base["long"] != "" else None
        credit = float(base["credit"]) if traded and base["credit"] != "" else None
        spx_close = float(base["spx_close"]) if base["spx_close"] != "" else None

        legs = None
        opt_path = OPT_DIR / f"spxw_p_cbbo_{ds}.parquet"
        if traded and opt_path.exists():
            legs = sim.build_leg_frames(pd.read_parquet(opt_path))

        for k in KS:
            rec = {
                "date": str(day), "weekday": day.strftime("%a"), "k": KLABEL[k],
                "traded": base["traded"], "short": base["short"], "long": base["long"],
                "credit": base["credit"], "skip_reason": base["skip_reason"],
            }
            if not traded:
                rec.update({"exit_reason": base["exit_reason"], "stop_minute": "",
                            "mark_or_settle": "", "pnl_pts": "", "pnl_usd": ""})
            elif legs is None or spx_close is None:
                rec.update({"exit_reason": "INCOMPLETE", "stop_minute": "",
                            "mark_or_settle": "", "pnl_pts": "", "pnl_usd": ""})
            else:
                er, sm, ms, pp, pu = resolve_exit(legs, short_k, long_k, credit, day, spx_close, k)
                rec.update({"exit_reason": er, "stop_minute": sm,
                            "mark_or_settle": ms, "pnl_pts": pp, "pnl_usd": pu})
            rows.append(rec)

    fields = ["date","weekday","k","traded","short","long","credit","skip_reason",
              "exit_reason","stop_minute","mark_or_settle","pnl_pts","pnl_usd"]
    tmp = str(OUT_CSV) + ".tmp"
    with open(tmp, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields); w.writeheader(); w.writerows(rows)
    Path(tmp).replace(OUT_CSV)

    # window totals per k
    df = pd.DataFrame(rows)
    df["pnl_usd_n"] = pd.to_numeric(df["pnl_usd"], errors="coerce")
    print("=== window totals per k (@ $0.10 slip) ===")
    for k in KS:
        sub = df[df["k"] == KLABEL[k]]
        tot = sub["pnl_usd_n"].sum()
        wins = (sub["exit_reason"] == "EXPIRED").sum()
        stops = (sub["exit_reason"] == "STOP").sum()
        print(f"  k={KLABEL[k]:>4}: total=${tot:>7.0f}   EXPIRED={wins} STOP={stops}")

    print("\n=== key days ===")
    for d in ["2026-06-23","2026-06-24","2026-07-02"]:
        for k in KS:
            r = df[(df["date"]==d) & (df["k"]==KLABEL[k])].iloc[0]
            print(f"  {d} k={KLABEL[k]:>4}: {r['exit_reason']:>8} "
                  f"min={r['stop_minute'] or '--':>5} mark/settle={r['mark_or_settle']} pnl=${r['pnl_usd']}")
    return df


if __name__ == "__main__":
    main()
