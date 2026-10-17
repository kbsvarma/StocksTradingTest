from pathlib import Path
import pandas as pd,numpy as np,json
P=Path(__file__).resolve().parent;q=pd.read_parquet(P/'long_eligible_prices.parquet');sy=['META','GOOGL','MSFT','NVDA','AMD','AVGO','AMZN','MU','DELL','INTC','TSM'];missing=[s for s in sy if s not in q];assert not missing,missing
ret=q.pct_change(fill_method=None);proxy=ret[sy].mean(axis=1).where(ret[sy].notna().all(axis=1));pool=pd.concat([pd.read_csv(P/f) for f in ['verification_pool_extended.csv','verification_pool_expanded.csv','verification_pool_long.csv']]).drop_duplicates(['a','b']);rows=[]
for _,r in pool.iterrows():
 for period,lo,hi in [('2017_on','2017-01-01','2026-09-11'),('discovery','2022-01-01','2025-12-31'),('confirmation','2026-01-01','2026-09-11')]:
  z=ret[[r.a,r.b]].loc[lo:hi].copy();z['proxy']=proxy;z=z.dropna()
  if len(z)<100:continue
  strategy=z[[r.a,r.b]].mean(axis=1)-.001*(z[r.a]-z[r.b]).abs()/2;rows.append(dict(a=r.a,b=r.b,period=period,days=len(z),within_pair_correlation=z[r.a].corr(z[r.b]),correlation_to_monitored_stock_proxy=strategy.corr(z.proxy),beta_to_monitored_stock_proxy=strategy.cov(z.proxy)/z.proxy.var()))
pd.DataFrame(rows).to_csv(P/'exposure_checks.csv',index=False);(P/'EXPOSURE_PROXY.json').write_text(json.dumps({'symbols':sy,'weights':'equal daily weights for correlation reference only, not actual user holdings','note':'Does not assume user position sizes or change the tested two-stock strategy.'},indent=2));print('DONE',len(rows),flush=True)
