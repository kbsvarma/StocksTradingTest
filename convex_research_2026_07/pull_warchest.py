import os
import databento as db
import pandas as pd
from pathlib import Path

c = db.Historical(os.environ["DATABENTO_API_KEY"])
OUT = Path('data'); OUT.mkdir(exist_ok=True)

jobs = [
    ('glbx_stats.parquet', dict(dataset='GLBX.MDP3',
        symbols=['ES.v.0','NQ.v.0','YM.v.0','GC.v.0','SI.v.0','HG.v.0','CL.v.0',
                 'ZN.v.0','6E.v.0','ZC.v.0','ZS.v.0','ZW.v.0','RTY.v.0','BTC.v.0'],
        stype_in='continuous', schema='statistics',
        start='2010-06-06', end='2026-07-04')),
    ('gc_1m_2010_2019.parquet', dict(dataset='GLBX.MDP3', symbols=['GC.v.0'],
        stype_in='continuous', schema='ohlcv-1m',
        start='2010-06-06', end='2019-07-01')),
    ('ifus_1h.parquet', dict(dataset='IFUS.IMPACT',
        symbols=['DX.v.0','CC.v.0','KC.v.0','SB.v.0','CT.v.0'],
        stype_in='continuous', schema='ohlcv-1h',
        start='2018-12-23', end='2026-07-04')),
    ('ifeu_1h.parquet', dict(dataset='IFEU.IMPACT',
        symbols=['BRN.v.0','GAS.v.0'],
        stype_in='continuous', schema='ohlcv-1h',
        start='2018-12-23', end='2026-07-04')),
]
for fname, kw in jobs:
    f = OUT / fname
    if f.exists():
        print(f"{fname}: cached", flush=True); continue
    try:
        d = c.timeseries.get_range(**kw)
        df = d.to_df()
        df.to_parquet(f)
        print(f"{fname}: {len(df):,} rows", flush=True)
    except Exception as e:
        print(f"{fname}: FAILED {str(e)[:150]}", flush=True)
print("WARCHEST COMPLETE", flush=True)
