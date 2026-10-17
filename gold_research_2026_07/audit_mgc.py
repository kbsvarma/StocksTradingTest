"""Audit the existing MGC 1-min cache. Facts only; no repairs, no fills."""
from pathlib import Path
import numpy as np
import pandas as pd

SRC = Path("../spx_spread_bot/backtests/gold_london/data/mgc_1m.csv")

def main():
    df = pd.read_csv(SRC, index_col=0)
    df.index = pd.to_datetime(df.index, utc=False)
    # timestamps carry -04:00/-05:00 offsets; normalize to ET
    df.index = pd.to_datetime(df.index, utc=True).tz_convert("America/New_York")
    df = df.sort_index()
    print(f"rows: {len(df):,}   range: {df.index[0]} -> {df.index[-1]}")
    dup = df.index.duplicated().sum()
    print(f"duplicate ts: {dup}")
    bad = ((df.high < df.low) | (df.high < df.open) | (df.high < df.close)
           | (df.low > df.open) | (df.low > df.close)).sum()
    print(f"OHLC violations: {bad}")
    print(f"non-positive px rows: {(df[['open','high','low','close']] <= 0).any(axis=1).sum()}")
    print(f"NaN rows: {df[['open','high','low','close']].isna().any(axis=1).sum()}")
    # trading-day coverage (CME day = 18:00 ET prev -> 17:00 ET; use calendar date of bar)
    days = pd.Series(df.index.date)
    per_year = days.groupby(pd.Series(df.index.year)).nunique()
    print("calendar days with bars, per year:", per_year.to_dict())
    # bars per day distribution (full day ~ 1380 for 23h session)
    bpd = days.groupby(days).size()
    print(f"bars/day: median={bpd.median():.0f}  p10={bpd.quantile(0.1):.0f}  min={bpd.min()}")
    thin = bpd[bpd < 200]
    print(f"days with <200 bars: {len(thin)}")
    if len(thin):
        print("  sample:", list(thin.index[:8]))
    # gap scan: business days missing entirely
    all_days = pd.date_range(df.index[0].date(), df.index[-1].date(), freq="B")
    have = set(days.unique())
    missing = [d.date() for d in all_days if d.date() not in have]
    print(f"weekday dates with NO bars: {len(missing)}")
    print("  sample:", missing[:10])
    # price continuity: largest 1-min log return (catch bad prints)
    lr = np.log(df.close / df.close.shift())
    big = lr.abs().nlargest(5)
    print("largest 1-min moves:")
    for ts, v in big.items():
        print(f"  {ts}  {v*100:+.2f}%  close={df.loc[ts,'close']}")

if __name__ == "__main__":
    main()
