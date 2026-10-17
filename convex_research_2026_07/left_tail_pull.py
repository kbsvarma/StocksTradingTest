"""T1 data pipeline — construct the exact SPY put contracts the ladder would
own (monthly 3rd-Friday expiries, strikes 10%/15% OTM at purchase), pull
ONLY those contracts' daily bars from OPRA. ~950 contracts over 13yr.
"""
import os
import sys
from pathlib import Path

import databento as db
import numpy as np
import pandas as pd

BASE = Path(__file__).parent
KEY = os.environ["DATABENTO_API_KEY"]
c = db.Historical(KEY)


def third_friday(year, month):
    d = pd.Timestamp(year=year, month=month, day=1)
    fridays = pd.date_range(d, d + pd.offsets.MonthEnd(0), freq="W-FRI")
    return fridays[2]


def spy_daily():
    """SPY proxy = official Cboe SPX / 10 (strikes round to $1, so the ~0.3%
    SPY-vs-SPX/10 dividend drift is immaterial for strike selection —
    disclosed). Covers 2013+ without an equity-feed dependency."""
    s = pd.read_csv(BASE.parent / "bps_rederive_2026_07/data/spx_daily_cboe.csv",
                    parse_dates=["DATE"]).set_index("DATE")["SPX"]
    return s / 10.0


def build_ladder():
    """Purchase schedule: first trading day each month, buy the put expiring
    ~90d out (3rd Friday, 3 months ahead), strikes 90% and 85% of spot."""
    spot = spy_daily()
    spot.index = pd.DatetimeIndex(spot.index).tz_localize(None) \
        if spot.index.tz is None else spot.index.tz_convert(None)
    buys = []
    for year in range(2013, 2027):
        for month in range(1, 13):
            d0 = pd.Timestamp(year=year, month=month, day=1)
            days = spot.index[(spot.index >= d0) & (spot.index < d0 + pd.Timedelta(days=7))]
            if not len(days):
                continue
            buy_day = days[0]
            exp_m = d0 + pd.offsets.MonthBegin(3)
            expiry = third_friday(exp_m.year, exp_m.month)
            if expiry > pd.Timestamp("2026-09-30") or buy_day > pd.Timestamp("2026-06-01"):
                continue
            s = float(spot.loc[buy_day])
            for depth, frac in (("d10", 0.90), ("d15", 0.85)):
                strike = round(s * frac / 5) * 5      # deep-OTM = $5 grid
                occ = f"SPY   {expiry:%y%m%d}P{int(strike*1000):08d}"
                buys.append(dict(buy_day=buy_day, expiry=expiry, depth=depth,
                                 spot=s, strike=strike, symbol=occ))
    return pd.DataFrame(buys)


def main():
    ladder = build_ladder()
    ladder.to_parquet(BASE / "data/left_tail_ladder.parquet")
    print(f"ladder: {len(ladder)} contracts, {ladder['buy_day'].nunique()} buy-months")
    # pull daily bars per contract, batched by year of buy_day
    out = BASE / "data/left_tail_bars"
    out.mkdir(exist_ok=True)
    total_cost = 0.0
    for year, g in ladder.groupby(ladder["buy_day"].dt.year):
        f = out / f"bars_{year}.parquet"
        if f.exists():
            print(f"{year}: cached"); continue
        syms = sorted(g["symbol"].unique())
        start = "2013-04-01" if year == 2013 else f"{year}-01-01"
        end = min(pd.Timestamp(f"{year}-12-31") + pd.Timedelta(days=120),
                  pd.Timestamp("2026-07-02")).strftime("%Y-%m-%d")
        # resolve-filter: drop constructed symbols OPRA never listed
        res = c.symbology.resolve(dataset="OPRA.PILLAR", symbols=syms,
                                  stype_in="raw_symbol",
                                  stype_out="instrument_id",
                                  start_date=start, end_date=end)
        syms = [s for s in syms if res["result"].get(s)]
        if not syms:
            print(f"{year}: NO symbols resolved — skipped"); continue
        cost = c.metadata.get_cost(dataset="OPRA.PILLAR", symbols=syms,
                                   stype_in="raw_symbol", schema="ohlcv-1d",
                                   start=start, end=end)
        total_cost += cost
        d = c.timeseries.get_range(dataset="OPRA.PILLAR", symbols=syms,
                                   stype_in="raw_symbol", schema="ohlcv-1d",
                                   start=start, end=end)
        df = d.to_df()
        df.to_parquet(f)
        print(f"{year}: {len(g)} contracts, {len(df)} bars, ${cost:.2f}")
    print(f"total pull cost: ${total_cost:.2f}")


if __name__ == "__main__":
    main()
