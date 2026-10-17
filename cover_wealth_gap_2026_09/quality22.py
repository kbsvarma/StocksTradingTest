from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import pandas as pd,numpy as np
P=Path(__file__).resolve().parent;V=P.parent/'cover_validation_2026_09'
def audit(f):
 s=f.stem;d=pd.read_parquet(f).loc['2004-09-13':];p=d['Adj Close'];r=np.log(p/p.shift());dv=d.Close*d.Volume
 return dict(symbol=s,first=str(d.index[0].date()),zero_volume_fraction=float(d.Volume.le(0).mean()),p10_dollar_volume=float(dv.quantile(.1)),median_dollar_volume=float(dv.median()),extreme_2x=int(r.abs().gt(np.log(2)).sum()),extreme_4x=int(r.abs().gt(np.log(4)).sum()),max_gross=float(np.exp(r.max())),min_gross=float(np.exp(r.min())))
with ThreadPoolExecutor(8) as pool:a=pd.DataFrame(pool.map(audit,sorted((V/'long_daily').glob('*.parquet'))))
a.to_csv(P/'historical_quality22.csv',index=False)
