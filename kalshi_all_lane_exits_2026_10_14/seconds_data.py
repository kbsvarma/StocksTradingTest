"""Preserve one-second source history, never fabricate order-book history."""
from core import *
def main():
 d=pickle.load(gzip.open(P/'inputs.pkl.gz'));seconds={};sources=[];quality={}
 for a in ASSETS:
  ms=[m for m in d['markets'].values() if m['asset']==a];start=int(min(m['start'] for m in ms))-7200;end=int(max(m['close'] for m in ms))+1
  seconds[a]=(start,np.full(end-start,np.nan))
 chartroots=[R/'kalshi_metals_research_2026_10_11/data/charts',R/'kalshi_frequency_expansion_2026_10_12/financial_inputs/data/charts',R/'kalshi_finance_expansion_2026_10_14/fx/data/charts',R/'kalshi_loss_review_2026_10_14/noncrypto_data/charts',R/'kalshi_z_exit_research_2026_10_14/fresh',P/'fresh']
 for root in chartroots:
  files=sorted(root.glob('*.json.gz'))+sorted(root.glob('*_chart.json'))
  for f in files:
   x=json.load(gzip.open(f)) if f.suffix=='.gz' else json.loads(f.read_text());raw=x.get('response',{}).get('live_data',{}).get('details',{});a=raw.get('asset')
   if a not in ASSETS or a in CRYPTO:continue
   arr=raw.get('timeseries',[]);start,values=seconds[a]
   if not arr:continue
   tt=np.array([int(x['t'])//1000-start for x in arr]);vv=np.array([float(x['v']) for x in arr]);ok=(tt>=0)&(tt<len(values));values[tt[ok]]=vv[ok];sources.append(str(f.relative_to(R)))
 for f in sorted((P/'cf_seconds').glob('*.gz')):
  a=f.name.split('_')[0];x=json.load(gzip.open(f));arr=x['response']['data']['payload'];start,values=seconds[a]
  pairs=[(int(x['time'])//1000-start,float(x['value'])) for x in arr if int(x['time'])%1000==0];tt=np.array([x[0] for x in pairs]);vv=np.array([x[1] for x in pairs]);ok=(tt>=0)&(tt<len(values));values[tt[ok]]=vv[ok];sources.append(str(f.relative_to(R)))
 for a,(start,values) in seconds.items():quality[a]=dict(start=start,end=start+len(values),seconds_present=int(np.isfinite(values).sum()))
 with gzip.open(P/'seconds.pkl.gz','wb',compresslevel=1) as f:pickle.dump(seconds,f)
 save('seconds_manifest.json',dict(quality=quality,sources=sources,meaning='Historical source timestamps; assumed1s delivery, no measured historical first-arrival lag. CryptoCF1Hz anchored to entry Coinbase spot; noncryptoPyth source exact.'))
 print('seconds',quality,flush=True)
if __name__=='__main__':main()
