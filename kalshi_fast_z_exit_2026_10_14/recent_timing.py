"""Recent Pyth source trigger audit ONLY. No inferred subminute exit prices."""
from pathlib import Path
import gzip,json,math,bisect,collections
P=Path(__file__).resolve().parent;R=P.parent;Z=R/'kalshi_z_exit_research_2026_10_14';O=R/'kalshi_loss_review_2026_10_14'
def main():
 old=json.load(gzip.open(Z/'paths.json.gz'));rows=[r for r in old['actual']+old['paused_cad'] if r['asset'] in ('WTI','NATGAS','EURUSD','USDCAD')]
 prices=collections.defaultdict(dict);revisions=collections.Counter()
 files=list(sorted((O/'noncrypto_data/charts').glob('*.gz')))+list(sorted((Z/'fresh').glob('*_chart.json')))
 for p in files:
  x=json.load(gzip.open(p)) if p.suffix=='.gz' else json.loads(p.read_text());d=x['response']['live_data']['details'];a=d['asset']
  for v in d['timeseries']:
   t=v['t']/1000;value=float(v['v'])
   if t in prices[a] and prices[a][t]!=value:revisions[a]+=1
   prices[a][t]=value
 times={a:sorted(p) for a,p in prices.items()};results={};records={}
 for delay in [1,3,5]:
  for interval in [1,10,30,60]:
   for drop in [.5,1,1.5,2]:
    key=f'interval{interval}_drop{drop:g}_source_delay{delay}';ledger=[]
    for r in rows:
     a=r['asset'];tt=times[a];found=None;missing=0
     for t in range((int(r['entered'])//interval+1)*interval,int(r['close']),interval):
      idx=bisect.bisect_right(tt,t-delay)-1
      if idx<0 or t-delay-tt[idx]>1:missing+=1;continue
      z=r['side']*(prices[a][tt[idx]]/r['reference']-1)/(r['entry_sigma']*math.sqrt((r['close']-t)/60))
      if z<r['entry_z']-drop:
       found=dict(check=t,source_t=tt[idx],seconds_after_entry=t-r['entered'],seconds_to_close=r['close']-t,z=z,spot=prices[a][tt[idx]]);break
     ledger.append(dict(ticker=r['ticker'],asset=a,won=r['won'],hold_net=r['hold_net'],entry_z=r['entry_z'],entry_sigma=r['entry_sigma'],trigger=found,missing=missing))
    records[key]=ledger;active=[r for r in ledger if r['asset']!='USDCAD'];results[key]=dict(n=len(active),losses=sum(not r['won'] for r in active),losers_flagged=sum(not r['won'] and r['trigger'] is not None for r in active),winners_flagged=sum(r['won'] and r['trigger'] is not None for r in active),missing_source_checks=sum(r['missing'] for r in active),cad=[r for r in ledger if r['asset']=='USDCAD'])
 (P/'recent_timing.json').write_text(json.dumps(dict(note='Trigger-only: no subminute sell-side depth or first-arrival receipts. NOT exit fills/PnL.',revisions=dict(revisions),results=results),indent=2)+'\n')
 with gzip.open(P/'recent_timing_ledgers.json.gz','wt') as f:json.dump(records,f)
 for key in ['interval1_drop1_source_delay1','interval10_drop1_source_delay1','interval30_drop1_source_delay1','interval60_drop1_source_delay1','interval1_drop2_source_delay1']:print(key,json.dumps(results[key]))
if __name__=='__main__':main()
