from pathlib import Path
import json, numpy as np,pandas as pd
from scipy.optimize import brentq
import pyarrow.parquet as pq
import databento as db
P=Path(__file__).resolve().parent.parent
q=pd.read_parquet(P/'cover_validation_2026_09/long_eligible_prices.parquet').loc['2004-09-13':'2026-09-11']
known=pd.read_parquet(P/'cover_wealth_gap_2026_09/common22_all_pairs.parquet');checks=[]
for a,b in [('CAR','INSM'),('GFI','INSM'),('HL','INSM'),('INSM','STAA'),('INSM','CNO')]:
 prices=q[[a,b]].to_numpy()
 for w in [.5,.55]:
  weights=np.array([w,1-w])
  for cost in [.001,.0025]:
   qty=weights/(1+cost)/prices[0]
   for i in range(1,len(prices)):
    before=qty*prices[i];total=before.sum()
    if i==len(prices)-1:wealth=total*(1-cost);break
    nav=brentq(lambda v:v+cost*np.abs(weights*v-before).sum()-total,0,total,xtol=1e-12);qty=weights*nav/prices[i]
   expected=known[(known.a==a)&(known.b==b)&known.weight_a.eq(w)&known.cost_bps.eq(cost*1e4)].iloc[0].rebalance_multiple
   assert np.isclose(wealth,expected,rtol=1e-9),(a,b,w,cost,wealth,expected)
   checks.append(dict(pair=a+'/'+b,weight=w,cost=cost,wealth=wealth,expected=expected))
f=next((P/'cover_wealth_gap_2026_09/vendor').rglob('*.dbn.zst'));store=db.DBNStore.from_file(f);sample=next(iter(store.to_df(count=2)));assert len(sample)==2
out=dict(status='passed',independent_portfolio_checks=len(checks),checks=checks,raw_databento_sample_rows=len(sample),root=str(P))
(P/'research_migration_2026_09_14/REMOTE_SMOKE_PASSED.json').write_text(json.dumps(out,indent=2));print(json.dumps({k:v for k,v in out.items() if k!='checks'}))
