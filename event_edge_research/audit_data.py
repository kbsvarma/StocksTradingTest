"""Integrity audit for the pulled ES/NQ data. Reports facts; asserts nothing that
would hide a problem. NO data is modified or filled — audit only."""
from pathlib import Path
import pandas as pd
import numpy as np

DATA = Path(__file__).parent / "data"

def load_series(tag, schema):
    files = sorted(DATA.glob(f"{tag}_{schema}_*.parquet"))
    dfs = [pd.read_parquet(f) for f in files]
    df = pd.concat(dfs).sort_index()
    return df, len(files)

def audit(tag, schema):
    df, nfiles = load_series(tag, schema)
    print(f"\n{'='*70}\n{tag} {schema}  ({nfiles} files)\n{'='*70}")
    print(f"rows              : {len(df):,}")
    print(f"index tz          : {df.index.tz}")
    print(f"date range        : {df.index[0]} -> {df.index[-1]}")
    print(f"columns           : {list(df.columns)}")

    # duplicates
    dups = df.index.duplicated().sum()
    print(f"duplicate ts      : {dups}")

    # OHLC sanity
    ohlc_bad = ((df.high < df.low) | (df.high < df.open) | (df.high < df.close) |
                (df.low > df.open) | (df.low > df.close)).sum()
    print(f"OHLC violations   : {ohlc_bad}")
    nonpos = ((df[['open','high','low','close']] <= 0).any(axis=1)).sum()
    print(f"non-positive px   : {nonpos}")
    nan_px = df[['open','high','low','close']].isna().any(axis=1).sum()
    print(f"NaN price rows    : {nan_px}")

    # rolls
    if 'instrument_id' in df.columns:
        n_instr = df.instrument_id.nunique()
        rolls = (df.instrument_id != df.instrument_id.shift()).sum() - 1
        print(f"distinct instr_id : {n_instr}   (roll transitions: {rolls})")

    # calendar coverage: trading days present per year
    days = pd.Series(df.index.normalize().unique())
    by_year = days.groupby(days.dt.year).count()
    print("trading days/yr   : " + ", ".join(f"{y}:{c}" for y, c in by_year.items()))

    # largest gaps between consecutive session-days (calendar days)
    dd = pd.Series(sorted(df.index.normalize().unique()))
    gaps = dd.diff().dt.days.dropna()
    big = gaps[gaps > 4].sort_values(ascending=False).head(5)
    if len(big):
        print("gaps >4 cal days  : " + ", ".join(
            f"{dd.iloc[i]:%Y-%m-%d}(+{int(g)}d)" for i, g in big.items()))
    else:
        print("gaps >4 cal days  : none")
    return df

def main():
    es_d = audit("ES", "ohlcv-1d")
    nq_d = audit("NQ", "ohlcv-1d")
    es_m = audit("ES", "ohlcv-1m")
    nq_m = audit("NQ", "ohlcv-1m")

    # Cross-check: daily close vs last RTH 1-min close on a sample of days
    print(f"\n{'='*70}\nCROSS-CHECK: ES daily close vs 1-min (sample 5 recent days)\n{'='*70}")
    es_m_et = es_m.tz_convert("America/New_York")
    for d in es_d.index[-5:]:
        day = d.tz_convert("America/New_York").date() if d.tz else d.date()
        mday = es_m_et[es_m_et.index.date == day]
        if len(mday) == 0:
            print(f"{day}: no 1-min bars"); continue
        # RTH close ~ 16:00 ET; take last bar at/before 16:00
        rth = mday.between_time("09:30", "16:00")
        last_close = rth.close.iloc[-1] if len(rth) else np.nan
        dclose = es_d.loc[d, 'close']
        print(f"{day}: daily_close={dclose:.2f}  1min_RTHclose={last_close:.2f}  diff={dclose-last_close:+.2f}")

if __name__ == "__main__":
    main()
