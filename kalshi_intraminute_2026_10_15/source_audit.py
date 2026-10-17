import collections,json,math,statistics
import numpy as np
import replay as r
def q(v):
 return dict(n=len(v),median=float(np.median(v)),p95=float(np.quantile(v,.95)),max=float(max(v))) if v else dict(n=0)
def main():
 out={}
 for sample in ('dense','sparse'):
  lag=collections.defaultdict(list);errors=collections.defaultdict(list);zerr=collections.defaultdict(list);missing=collections.Counter()
  prices={};recompute=[];opened=collections.defaultdict(set)
  for a in r.ASSETS:
   path=(r.R/'kalshi_loss_review_2026_10_14/recent_data' if sample=='dense' else r.R/'kalshi_z_time_research_2026_10_03/input_sources/0')/f'spot_{a}.jsonl'
   pp={}
   for line in path.open():
    for row in json.loads(line)['rows']:pp[int(row[0])]=float(row[4])
   prices[a]=pp
  for b in r.bundles(sample):
   m=b['market'];a=m['asset'];fs={f['t']:f for f in m['features']};fn=r.source_fn(b,1)
   opened[a].add(m['start'])
   for recv,src,value in b.get('cf',[]):lag[a].append((recv-src)*1000)
   for t,f in fs.items():
    vv=[prices[a].get(x) for x in range(int(t)-3660,int(t),60)];ref=prices[a].get(int(m['start'])-60)
    if all(v is not None for v in vv) and ref is not None:
     sigma=statistics.pstdev(math.log(y/x) for x,y in zip(vv,vv[1:]));z=f['side']*(vv[-1]/ref-1)/(sigma*math.sqrt(f['rem']))
     recompute.append(abs(z-f['z']))
     assert abs(z-f['z'])<1e-7
    nxt=fs.get(t+60)
    if not nxt:continue
    value=fn(t+60-.001,f)
    if value is None:missing[a]+=1;continue
    errors[a].append(abs(value/nxt['spot']-1)*1e4)
    if nxt['rem']>0:zerr[a].append(abs(value-nxt['spot'])/f['ref']/(f['sigma']*math.sqrt(nxt['rem'])))
  out[sample]={a:dict(actual_receipt_lag_ms=q(lag[a]),anchor_tracking_error_bps=q(errors[a]),equivalent_z_error=q(zerr[a]),missing_pairs=missing[a]) for a in r.ASSETS}
  out[sample]['independent_coinbase_formula_recomputation']=dict(count=len(recompute),maximum_difference=max(recompute) if recompute else None)
  if sample=='sparse':
   out[sample]['unarchived_market_open_times']={a:sorted(set(range(r.START,r.END,900))-opened[a]) for a in r.ASSETS}
 r.save('source_audit.json',out);print(json.dumps(out,indent=2))
if __name__=='__main__':main()
