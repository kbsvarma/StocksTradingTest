from pathlib import Path
import pandas as pd,numpy as np,json
P=Path(__file__).resolve().parent;x=pd.read_parquet(P/'trades.parquet');x=x[(x['mode']=='recovered')&(x.clock=='standard')&(x.cost=='three_cents')].sort_values('day');r=np.load(P/'random_controls.npz');sel=r['modes']=='recovered';rd=r['days'][sel];rn=r['net'][sel];rng=np.random.default_rng(946);out=[]
for sample in ['all_dates','model_dates']:
 h=x if sample=='all_dates' else x[x.model_selected]
 for period,lo,hi in [('development','2019','2023'),('validation','2023','2025'),('recent','2025','2027'),('all','2018','2027')]:
  g=h[(h.day>=lo)&(h.day<hi)];m=g.groupby(g.day.str[:7]).agg(net=('net','sum'),filled=('filled','sum'));j=rng.integers(len(m),size=(10000,len(m)));boot=m.net.to_numpy()[j].sum(1)/np.maximum(m.filled.to_numpy()[j].sum(1),1);random=rn[np.isin(rd,g.day)].sum(0);out.append(dict(sample=sample,period=period,net=g.net.sum(),ci_low=np.quantile(boot,.025),ci_high=np.quantile(boot,.975),random_median=np.median(random),random_p025=np.quantile(random,.025),random_p975=np.quantile(random,.975),fraction_random_below=(random<g.net.sum()).mean()))
pd.DataFrame(out).to_csv(P/'uncertainty_controls.csv',index=False)
l=pd.read_parquet(P/'legs.parquet');l=l[(l['mode']=='recovered')&(l.clock=='standard')];l['year']=l.day.str[:4];l.groupby(['year','symbol','side']).agg(trades=('day','size'),net=('net','sum')).to_csv(P/'symbol_contributions.csv');l.nlargest(20,'net').to_csv(P/'best_legs.csv',index=False);l.nsmallest(20,'net').to_csv(P/'worst_legs.csv',index=False)
c=pd.read_csv(P/'coverage.csv');c=c[(c['mode']=='recovered')&(c.clock=='standard')];(P/'audit.json').write_text(json.dumps(dict(days=len(c),unfilled=int((~c.filled).sum()),missing_exit=int(c.missing_exit.sum()),start=c.day.min(),end=c.day.max()),indent=2));print(pd.DataFrame(out).to_string(index=False));print((P/'audit.json').read_text())
