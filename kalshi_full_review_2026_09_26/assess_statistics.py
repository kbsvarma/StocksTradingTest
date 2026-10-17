import json,collections,random,math
from pathlib import Path
P=Path(__file__).resolve().parent;O=P/'results'
def pvalue(values):
 if len(values)<5 or sum(values)<=0:return 1.
 rng=random.Random(7301);target=sum(values);ge=0
 for _ in range(10000):
  if sum(x*(1 if rng.getrandbits(1) else -1) for x in values)>=target-1e-12:ge+=1
 return (ge+1)/10001
rows=[]
for r in json.loads((O/'replay_results_open.json').read_text()):
 path=O/(r['series']+'_'+r['rule']+'_open_trades.json');tr=json.loads(path.read_text());ds=collections.defaultdict(float)
 for t in tr:ds[t['day']]+=t['stress1']
 u=r['unconstrained'];n=len(tr);lb=.05**(1/n) if n and all(t['won'] for t in tr) else None
 rows.append({'candidate':r['series']+'/'+r['rule'],'type':'prediction','p':pvalue(list(ds.values())),'days':len(ds),'all_win_lower95':lb,'breakeven_win_probability':sum(t['cost']+.02 for t in tr)/(2*n) if n else None})
for r in json.loads((O/'perps_results.json').read_text()):
 tr=json.loads((O/(r['ticker']+'_'+r['rule']+'_perp_trades.json')).read_text());ds=collections.defaultdict(float)
 for t in tr:ds[t['day']]+=t['stress']
 rows.append({'candidate':r['ticker']+'/'+r['rule'],'type':'perpetual','p':pvalue(list(ds.values())),'days':len(ds)})
rank=sorted(range(len(rows)),key=lambda i:rows[i]['p']);running=1.
for j in range(len(rank)-1,-1,-1):
 i=rank[j];running=min(running,rows[i]['p']*len(rows)/(j+1));rows[i]['bh_q']=running;rows[i]['bonferroni_p']=min(1,rows[i]['p']*len(rows))
(O/'selection_audit.json').write_text(json.dumps({'method':'Exploratory day-block Rademacher sign test, 10000 draws; assumes symmetric independent daily errors. Not proof of edge; zero-loss tail check separate. All declared candidate combinations included.','hypotheses':len(rows),'rows':rows},indent=2))
for r in sorted(rows,key=lambda r:r['p'])[:12]:print(r)
