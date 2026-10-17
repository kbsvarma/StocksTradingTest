"""Combine fixed tennis lead and quantify uncertainty without picking variants."""
from tennis_markov import P,read,save,simulate,stats,ts
import random,datetime as dt,collections
PREFIX='blend0.5/edge0.06/'
a=read(P/'tennis_adaptive100_candidates.json.gz');b=read(P/'tennis_older_candidates.json.gz')
results={};ledgers={};rng=random.Random(10112026)
for lag in (30,90):
 for execution in ('base','delay60'):
  key=PREFIX+f'lag{lag}/'+execution
  raw=a.get(key,[])+b.get(key,[])
  for cost in (.01,.02):
   rr,skip=simulate([r for r in raw if '2026-09-12'<=r['date']<='2026-10-09'],1,cost)
   k=key+f'/combined/cost{int(cost*100)}';s=stats(rr);s.update(trades_per_day=len(rr)/28,skips=skip)
   days=[(dt.date(2026,9,12)+dt.timedelta(days=i)).isoformat() for i in range(28)];pnls=[s['daily'].get(d,0.) for d in days]
   # Two-day circular block bootstrap is descriptive and NOT corrected for
   # strategy selection or missing receipt timestamps / execution assumptions.
   draws=[]
   for _ in range(20000):
    total=0.
    for j in range(14):
     i=rng.randrange(28);total+=pnls[i]+pnls[(i+1)%28]
    draws.append(total)
   draws.sort();s['descriptive_block_bootstrap_total_95']=[draws[500],draws[19499]]
   s['by_tour']={asset:stats([r for r in rr if r['asset']==asset]) for asset in sorted({r['asset'] for r in rr})}
   s['gross_before_fee_friction']=sum(r['payout']-r['entry'] for r in rr);s['fees']=sum(r['fee'] for r in rr);s['friction_total']=len(rr)*cost
   s['mean_entry']=sum(r['entry'] for r in rr)/len(rr) if rr else None
   s['average_model_probability']=sum(r['prediction'] for r in rr)/len(rr) if rr else None
   results[k]=s;ledgers[k]=rr;print(k,s['trades'],s['net'],s['drawdown'],s['descriptive_block_bootstrap_total_95'])
save('tennis_lead_combined_results.json',results);save('tennis_lead_combined_ledgers.json.gz',ledgers)
