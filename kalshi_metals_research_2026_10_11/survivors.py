"""Report previously frozen early-period survivors without changing the selected winners."""
import gzip,json,pickle
import study as s
P=s.P;markets=pickle.loads((P/'features.pickle').read_bytes());pred=pickle.loads((P/'predictions.pickle').read_bytes());frozen=json.loads((P/'choices_frozen.json').read_text())['choices'];results={};ledgers={}
for a,ch in frozen.items():
 results[a]={};ledgers[a]={}
 for cid in ch['survivors']:
  c=s.CONFIG[cid];results[a][str(cid)]={};ledgers[a][str(cid)]={}
  for mode,stress,name in (('base',.01,'base'),('base',.02,'cost2'),('base',.03,'cost3'),('cap',.01,'cap'),('delay60',.01,'delay60')):
   pool,_=s.opportunities(markets,c,pred,mode);results[a][str(cid)][name]={};ledgers[a][str(cid)][name]={}
   for p in s.PERIODS:
    m,l=s.evaluate(pool,a,p,stress);results[a][str(cid)][name][p]=m;ledgers[a][str(cid)][name][p]=l
  print(a,cid,c['name'],{p:tuple(results[a][str(cid)]['base'][p][k] for k in ('trades','wins','losses','net','drawdown')) for p in s.PERIODS},flush=True)
s.save('survivor_results.json',results)
with gzip.open(P/'survivor_ledgers.json.gz','wt') as f:json.dump(ledgers,f)
