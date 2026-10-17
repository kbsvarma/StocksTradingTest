from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import pandas as pd,numpy as np
P=Path(__file__).resolve().parent;V=P.parent/'cover_validation_2026_09'
def audit(f):
 s=f.stem;d=pd.read_parquet(f);p=d['Adj Close'];r=np.log(p/p.shift());dv=d.Close*d.Volume
 return dict(symbol=s,first=str(d.index[0].date()),zero_volume_fraction=float(d.Volume.le(0).mean()),p10_dollar_volume=float(dv.quantile(.1)),median_dollar_volume=float(dv.median()),extreme_2x=int(r.abs().gt(np.log(2)).sum()),extreme_4x=int(r.abs().gt(np.log(4)).sum()),max_gross=float(np.exp(r.max())),min_gross=float(np.exp(r.min())))
with ThreadPoolExecutor(8) as pool:a=pd.DataFrame(pool.map(audit,sorted((V/'long_daily').glob('*.parquet'))))
a.to_csv(P/'historical_quality.csv',index=False);a=a.set_index('symbol');x=pd.read_parquet(P/'eight_year_candidates.parquet')
for side in ['a','b']:
 for c in a.columns:x[side+'_'+c]=x[side].map(a[c])
x['historical_tradeability_proxy']=(x.a_zero_volume_fraction.le(.01)&x.b_zero_volume_fraction.le(.01)&x.a_p10_dollar_volume.ge(1e5)&x.b_p10_dollar_volume.ge(1e5)&x.a_extreme_4x.eq(0)&x.b_extreme_4x.eq(0))
x.to_parquet(P/'annotated_candidates.parquet',index=False)
y=x[x.historical_tradeability_proxy];y.head(200).to_csv(P/'top200_history_checked.csv',index=False)
z=y[y.stock_a_multiple.le(15)&y.stock_b_multiple.le(15)&y.rebalance_multiple.ge(10)].sort_values('over_best',ascending=False);z.head(100).to_csv(P/'ordinary_stock_outcomes.csv',index=False)
for name,g in [('history_checked',y),('ordinary_outcomes',z),('gap8_lowest_drawdown',y[y.over_best.ge(8)].sort_values('full_maxdd_5bps',ascending=False)),('nonai_gap4_lowest_drawdown',y[y.no_ai&y.over_best.ge(4)].sort_values('full_maxdd_5bps',ascending=False))]:
 print(name,len(g),flush=True);print(g.head(15)[['a','b','full_years','stock_a_multiple','stock_b_multiple','rebalance_multiple','over_best','full_maxdd_5bps']].to_string(index=False),flush=True)
