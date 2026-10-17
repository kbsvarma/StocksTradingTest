"""Acceptance test: engine vs the 10 real live fills.
For each live trade: load that day's chain, take the LIVE entry minute (from bot
logs) and LIVE strikes, and compare:
  1. engine NBBO credit at that minute vs actual fill credit
  2. engine strike selection (parity spot, live rules) vs actual strikes
  3. SL behavior: engine cost-to-close path vs live outcome (expired / stopped, exit px)
  4. settlement P&L vs live P&L
Engine results carry no weight until this passes (see PREREGISTRATION_BPS.md).
"""
import numpy as np
import pandas as pd
from chain import load_day, parity_spot
from engine import DayContext, Config, sim_day, round5, build_minute_grid

ET = "America/New_York"

# (date, entry_hh, entry_mm from logs; strikes, fill credit, outcome, exit_px, live_spx_at_entry)
LIVE = [
    # times from system.log fill/monitor-start lines (see reconciliation notes)
    ("20260513", 10, 58, 7350, 7300, 0.65, "EXPIRED", 0.0, 7411.43),   # monitor 10:59:04
    ("20260514", 11, 0, 7435, 7385, 1.05, "EXPIRED", 0.0, 7494.45),    # monitor 11:01:53 (re-signal after 10:30 no-fill)
    ("20260515", 10, 42, 7360, 7310, 0.75, "STOP", 1.50, 7433.55),     # monitor 10:42:28
    ("20260520", 10, 30, 7320, 7270, 0.45, "EXPIRED", 0.0, 7398.45),   # orphan-fill MARKET fallback (time approx)
    ("20260520", 11, 0, 7355, 7305, 0.90, "EXPIRED", 0.0, 7401.20),    # force B1 (time approx)
    ("20260520", 11, 5, 7355, 7305, 0.90, "EXPIRED", 0.0, 7402.10),    # force B2 (time approx)
    ("20260521", 11, 31, 7360, 7310, 2.10, "EXPIRED", 0.0, 7410.86),   # monitor 11:32:32
    ("20260522", 10, 31, 7420, 7370, 2.00, "EXPIRED", 0.0, 7482.13),   # signal 10:30:16, fill ~10:31
    ("20260604", 10, 38, 7505, 7455, 1.20, "EXPIRED", 0.0, 7553.92),   # entry_ts 10:38:40
    ("20260616", 11, 7, 7495, 7445, 1.05, "STOP", 2.10, 7545.27),      # FILLED 11:07:18
]

def main():
    spx = pd.read_csv("data/spx_daily_cboe.csv", parse_dates=["DATE"]).set_index("DATE")["SPX"]
    vix = pd.read_parquet("data/vix_daily_cboe.parquet")
    print(f"{'date':<10}{'strikes':<12}{'live_cr':>8}{'eng_bid/ask_cr':>16}{'mid_cr':>8}"
          f"{'live_spx':>9}{'parity':>9}{'eng_strikes':>13}{'outcome_match':>14}")
    for (tag, hh, mm, ks, kl, cr, outcome, exit_px, live_spx) in LIVE:
        day = load_day(tag)
        if day is None:
            print(f"{tag}: NO CHAIN DATA"); continue
        d0 = pd.Timestamp(tag).tz_localize(ET)
        t = d0 + pd.Timedelta(hours=hh, minutes=mm)
        pbid, pask = build_minute_grid(day["P"])
        if t not in pbid.index:
            # snap to nearest available minute
            avail = pbid.index[(pbid.index >= t - pd.Timedelta(minutes=3)) & (pbid.index <= t + pd.Timedelta(minutes=3))]
            if len(avail) == 0:
                print(f"{tag}: no minutes near {hh}:{mm}"); continue
            t = avail[0]
        def q(grid, k):
            return grid.at[t, k] if k in grid.columns else np.nan
        sb, sa = q(pbid, ks), q(pask, ks)
        lb, la = q(pbid, kl), q(pask, kl)
        cr_cross = sb - la          # conservative: hit bid / lift ask
        cr_mid = (sb + sa) / 2 - (lb + la) / 2
        pspot, _ = parity_spot(day, t)
        # engine strike selection at this minute (live rule: 1% OTM round5, width 50)
        eng_ks = round5(pspot * 0.99) if not np.isnan(pspot) else np.nan
        eng_kl = eng_ks - 50 if not np.isnan(eng_ks) else np.nan
        # engine SL check on live strikes with live credit
        after = pbid.index[pbid.index > t]
        cost = pask.loc[after, ks] - pbid.loc[after, kl] if ks in pask.columns and kl in pbid.columns else pd.Series(dtype=float)
        trig = cost[(~cost.isna()) & (cost >= 2.0 * cr)]
        eng_outcome = "STOP" if len(trig) else "EXPIRED"
        eng_sl_time = str(trig.index[0].time()) if len(trig) else ""
        match = "OK" if eng_outcome == outcome else f"MISMATCH({eng_outcome}{' '+eng_sl_time if eng_sl_time else ''})"
        print(f"{tag:<10}{f'{ks:.0f}/{kl:.0f}':<12}{cr:>8.2f}{f'{cr_cross:.2f}/{sa-lb:.2f}':>16}"
              f"{cr_mid:>8.2f}{live_spx:>9.2f}{pspot:>9.2f}{f'{eng_ks:.0f}/{eng_kl:.0f}':>13}{match:>14}"
              + ("" if eng_outcome != "STOP" or outcome != "STOP" else f"  sl@{eng_sl_time} exit_cost={trig.iloc[0]:.2f} vs live {exit_px:.2f}"))

if __name__ == "__main__":
    main()
