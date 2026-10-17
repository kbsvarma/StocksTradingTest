"""T2 — RIGHT TAIL: energetic trend lottery per PREREG_TWO_TAILS.md.

At 10:30/11:30/12:30 ET (first trigger wins, max 1 ticket/day):
  IF ES hourly close > prior 20-hour max (state at the latest completed
  hourly bar) AND [variant] prior-day VIX > 20.72 (frozen):
    buy 1 SPXW same-day call, strike ~X% OTM (X in {0.25,0.5,1.0}),
    fill AT THE ASK (real NBBO); settle at official Cboe SPX close.
P&L per ticket = (max(S_close - K, 0) - ask) * 100.
Grid: 3 OTM x 2 triggers = 6 pre-registered cells. Window 2016+.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

BASE = Path(__file__).parent
sys.path.insert(0, str(BASE.parent / "trend_bot"))
sys.path.insert(0, str(BASE.parent / "trend_bot/research"))
sys.path.insert(0, str(BASE.parent / "bps_rederive_2026_07"))
import rotation_grid as rg
from chain import load_day, parity_spot, quote_at

ET = "America/New_York"
VIX_ENERGETIC = 20.72
DECISION_HOURS = (10, 11, 12)          # ticket at HH:30, state from HH:00 bar


def es_breakout_state():
    """UTC-hour bar timestamps where close > prior 20-bar max (ES)."""
    B = rg.load_60m("ES")
    donch = B["close"].rolling(20).max().shift(1)
    sig = B["close"] > donch
    return set(B.index[sig])


def vix_prior():
    vix = pd.read_parquet(BASE.parent / "bps_rederive_2026_07/data/vix_daily_cboe.parquet")
    vc = vix["close"] if "close" in vix.columns else vix.iloc[:, -1]
    if vc.index.tz is None:
        vc.index = pd.DatetimeIndex(vc.index).tz_localize("UTC")
    return vc


def spx_close():
    s = pd.read_csv(BASE.parent / "bps_rederive_2026_07/data/spx_daily_cboe.csv",
                    parse_dates=["DATE"]).set_index("DATE")["SPX"]
    return s


def main():
    sig_bars = es_breakout_state()
    vc = vix_prior()
    closes = spx_close()
    days = sorted(closes.index)
    days = [d for d in days if d >= pd.Timestamp("2016-01-01")]

    rows = []
    n_days_chain = 0
    for d in days:
        tag = d.strftime("%Y%m%d")
        official = closes.get(d, np.nan)
        if not np.isfinite(official):
            continue
        # prior-day VIX
        vd = vc[vc.index < pd.Timestamp(tag, tz="UTC") + pd.Timedelta(hours=23)]
        vix = float(vd.iloc[-1]) if len(vd) else np.nan
        day = None
        for hh in DECISION_HOURS:
            # ES state at the completed HH:00 ET bar (UTC-aligned hourly bars)
            bar_et = pd.Timestamp(tag, tz=ET) + pd.Timedelta(hours=hh)
            bar_utc = bar_et.tz_convert("UTC") - pd.Timedelta(hours=1)  # bar START label
            if bar_utc not in sig_bars:
                continue
            if day is None:
                day = load_day(tag)
                if day is None:
                    break
                n_days_chain += 1
            minute = bar_et + pd.Timedelta(minutes=30)
            s, n = parity_spot(day, minute)
            if not np.isfinite(s) or n < 3:
                continue
            c = quote_at(day["C"], minute)
            c = c[(c.bid_px_00 >= 0) & (c.ask_px_00 > 0)]
            for otm in (0.0025, 0.005, 0.01):
                k_target = round(s * (1 + otm) / 5) * 5
                row = c.iloc[(c["strike"] - k_target).abs().argsort()[:1]]
                if not len(row) or abs(row["strike"].iloc[0] - k_target) > 5:
                    continue
                ask = float(row["ask_px_00"].iloc[0])
                if ask <= 0 or ask > 100:
                    continue
                K = float(row["strike"].iloc[0])
                payoff = max(official - K, 0.0)
                rows.append(dict(date=d, hour=hh, otm=otm, K=K, ask=ask,
                                 spot=s, vix=vix, settle=official,
                                 pnl=(payoff - ask) * 100))
            break   # one ticket-decision per day (first triggered hour)
    F = pd.DataFrame(rows)
    F.to_parquet(BASE / "data/right_tail_tickets.parquet")
    print(f"ticket-days with chains: {n_days_chain}   ticket rows: {len(F)}")

    END22 = pd.Timestamp("2022-12-31")
    print(f"\n{'cell':<28}{'n':>6}{'total':>10}{'win%':>7}{'top-decile share':>18}{'halves':>18}")
    for otm in (0.0025, 0.005, 0.01):
        for gated in (False, True):
            X = F[F["otm"] == otm]
            if gated:
                X = X[X["vix"] > VIX_ENERGETIC]
            if not len(X):
                continue
            tot = X["pnl"].sum()
            wins = (X["pnl"] > 0).mean() * 100
            srt = X["pnl"].sort_values(ascending=False)
            top = srt.head(max(1, len(X)//10)).sum()
            share = top / X[X['pnl']>0]['pnl'].sum() * 100 if (X['pnl']>0).any() else 0
            h1 = X[X["date"] <= END22]["pnl"].sum()
            h2 = X[X["date"] > END22]["pnl"].sum()
            label = f"{otm*100:.2f}% OTM {'trend+energetic' if gated else 'trend-only'}"
            print(f"{label:<28}{len(X):>6}{tot:>10,.0f}{wins:>6.0f}%"
                  f"{share:>17.0f}%   ${h1:,.0f}/${h2:,.0f}")


if __name__ == "__main__":
    main()
