"""Expanded exit families on the independently archived continuous book cohort."""
from core import *
import importlib.util,bisect
spec=importlib.util.spec_from_file_location('old_replay',R/'kalshi_fast_z_exit_2026_10_14/replay.py');old=importlib.util.module_from_spec(spec);spec.loader.exec_module(old)
def simulate(r,c,interval,friction=.01,latency=1):
 out=dict(ticker=r['ticker'],asset=r['asset'],lane=r['lane'],won=r['won'],quantity=r['quantity'],hold=r['hold_net'],net=r['hold_net'],delta=0,exit=False,trigger=None,exit_fee=0,friction=0,price=None,source_missing=0,quote_missing=False,realized=r['settled']);start=None;books=r['b']['books'];bt=r['bt'];si=3 if r['side']==1 else 4
 for t in range((int(r['entered'])//interval+1)*interval,int(r['close']),interval):
  if t<r['entered']+c['grace']:continue
  bi=bisect.bisect_right(bt,t*1000)-1
  if c['family']=='price':
   sale=old.sale(books[bi][si],r['quantity']) if bi>=0 and t*1000-bt[bi]<=500 else None;v=sale['gross']/r['quantity'] if sale else None
  else:v=r['z'].get(t,{}).get('5hz')
  if v is None or v>=threshold(r,c):start=None;continue
  if start is None:start=t
  if t-start<c['persist']:continue
  ai=bisect.bisect_left(bt,(t+latency)*1000)
  if bi<0 or ai>=len(books) or t*1000-bt[bi]>500 or bt[ai]-(t+latency)*1000>500 or bt[ai]>=r['close']*1000:continue
  a=old.sale(books[bi][si],r['quantity']);b=old.sale(books[ai][si],r['quantity'])
  if a is None or b is None:continue
  fill=min([a,b],key=lambda z:z['proceeds']);net=fill['proceeds']-r['quantity']*friction-r['cost']-r['entry_fee']-r.get('entry_friction',0)
  out.update(net=net,delta=net-r['hold_net'],exit=True,trigger=t,exit_fee=fill['fee'],friction=r['quantity']*friction,price=fill['gross']/r['quantity'],realized=bt[ai]/1000,depth_levels=fill['levels']);break
 return out
def main():
 raw=pickle.load(gzip.open(R/'kalshi_fast_z_exit_2026_10_14/inputs.pkl.gz'));data=pickle.load(gzip.open(P/'inputs.pkl.gz'));lookup={r['ticker']:r for r in data['actual']};rr=[old.prepare(dict(r,entry_gate=lookup[r['ticker']]['entry_gate'],settled=lookup[r['ticker']]['settled']),raw['bundles'][r['ticker']],raw['prices'][r['asset']]) for r in raw['cohorts']['actual']];results=[];ledgers={}
 for interval in [1,10,30,60]:
  for c in rules():
   xs=[simulate(r,c,interval) for r in rr];key=f'i{interval}_'+c['id'];results.append(dict(id=key,interval=interval,config=c,metrics=metrics(xs)));ledgers[key]=xs
  print('depth done',interval,flush=True)
 save('depth_grid_results.json',results)
 with gzip.open(P/'depth_grid_ledgers.json.gz','wt') as f:json.dump(ledgers,f)
if __name__=='__main__':main()
