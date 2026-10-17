from pathlib import Path
import pandas as pd,numpy as np
P=Path(__file__).resolve().parent;rng=np.random.default_rng(84631)
p=pd.read_parquet(P/'mega/predictions.parquet');p=p[p.penalty==100];t=pd.read_parquet(P/'mega/trades.parquet');t=t[(t.penalty==100)&(t.cents==.03)&(t.cushion==0)].copy();l=pd.read_parquet(P/'mega/legs.parquet');l=l[(l.penalty==100)&(l.cents==.03)&(l.cushion==0)];extra=l.groupby('day').shares.apply(lambda x:x.abs().sum())*.04;t['stress']=t.net-t.day.map(extra).fillna(0);t['original_bps']=t.net # separately recompute below
matched=l.merge(p[['day','symbol','entry','exit','missing_exit']],on=['day','symbol'],validate='many_to_one');exit_for_cost=matched.exit.where(~matched.missing_exit,matched.entry);matched['bps_net']=matched.gross-matched.shares.abs()*.0005*(matched.entry+exit_for_cost);bps=matched.groupby('day').bps_net.sum();t['original_bps']=t.day.map(bps).fillna(0)
rows=[]
for day,g in p.groupby('day'):
 if not t.set_index('day').active.get(day,False):continue
 g=g.reset_index(drop=True);n=len(g);shares=np.floor(50000/g.entry.to_numpy());gross=shares*(g.exit-g.entry).to_numpy();fees=shares*(.06+.00005*(g.entry+g.exit).to_numpy());missing=g.missing_exit.to_numpy();long=np.where(missing,-shares*g.entry.to_numpy()*.1,gross)-fees;short=np.where(missing,-shares*g.entry.to_numpy()*.1,-gross)-fees
 # Missing exits have no observed liquidation price: retain modeled costs at entry.
 fees=np.where(missing,shares*(.06+.0001*g.entry.to_numpy()),fees);long=np.where(missing,-shares*g.entry.to_numpy()*.1,gross)-fees;short=np.where(missing,-shares*g.entry.to_numpy()*.1,-gross)-fees
 i=g.r.idxmax();j=g.r.idxmin();rows.append(dict(day=day,kind='momentum',trial=-1,net=long[i]+short[j]));i0=rng.integers(n,size=500);j0=(i0+rng.integers(1,n,size=500))%n
 rows.extend(dict(day=day,kind='random',trial=k,net=long[i0[k]]+short[j0[k]]) for k in range(500))
c=pd.DataFrame(rows);c.to_parquet(P/'matched_controls.parquet');summary=[]
for period,lo,hi in [('development','2019','2023'),('validation','2023','2025'),('recent','2025','2027')]:
 a=t[(t.day>=lo)&(t.day<hi)].sort_values('day');z=a.net;active=a[a.active];monthly=a.groupby(a.day.str[:7]).net.sum();counts=a.groupby(a.day.str[:7]).active.sum();idx=rng.integers(len(monthly),size=(5000,len(monthly)));boot=monthly.to_numpy()[idx].sum(1)/np.maximum(counts.to_numpy()[idx].sum(1),1);cc=c[(c.day>=lo)&(c.day<hi)];random=cc[cc.kind=='random'].groupby('trial').net.sum();summary.append(dict(period=period,n=len(active),net=z.sum(),stress_net=a.stress.sum(),five_bps_net=a.original_bps.sum(),without_best_five=z.sum()-z.nlargest(5).sum(),ci_low=np.quantile(boot,.025),ci_high=np.quantile(boot,.975),momentum_net=cc[cc.kind=='momentum'].net.sum(),random_median=random.median(),random_p95=random.quantile(.95),fraction_random_below_model=(random<z.sum()).mean(),missing=active.missing.sum()))
pd.DataFrame(summary).to_csv(P/'candidate_assessment.csv',index=False);print(pd.DataFrame(summary).to_string(index=False))
