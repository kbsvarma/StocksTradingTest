"""All-trade fast signals; quote envelopes rather than fabricated fast fills."""
from core import *
def main():
 d=pickle.load(gzip.open(P/'inputs.pkl.gz'));ss=pickle.load(gzip.open(P/'seconds.pkl.gz'));rr=d['actual'];configs=rules();all_results=[];ids=[];low=np.empty((1280,len(rr)));high=np.empty_like(low);triggers=np.full_like(low,-1);coverage=[];examples={};index=0
 paths={interval:{r['ticker']:path(r,d,interval,ss) for r in rr} for interval in [1,10,30,60]}
 for r in rr:
  pp=paths[1][r['ticker']];coverage.append(dict(ticker=r['ticker'],asset=r['asset'],won=r['won'],checks=len(pp['t']),missing_source=int(np.isnan(pp['z']).sum()),missing_low=int(np.isnan(pp['low']).sum()),missing_high=int(np.isnan(pp['high']).sum())))
 for interval in [1,10,30,60]:
  for c in configs:
   rowslow=[exit_result(r,c,paths[interval][r['ticker']],'low') for r in rr];rowshigh=[exit_result(r,c,paths[interval][r['ticker']],'high') for r in rr];key=f'i{interval}_'+c['id'];ids.append(key)
   low[index]=[r['net'] for r in rowslow];high[index]=[r['net'] for r in rowshigh];triggers[index]=[r['trigger'] or -1 for r in rowslow];index+=1
   all_results.append(dict(id=key,interval=interval,config=c,low=metrics(rowslow),high=metrics(rowshigh),early_low=metrics([x for r,x in zip(rr,rowslow) if r['close']<SPLIT]),later_low=metrics([x for r,x in zip(rr,rowslow) if r['close']>=SPLIT]),assets={a:dict(low=metrics([x for x in rowslow if x['asset']==a]),high=metrics([x for x in rowshigh if x['asset']==a])) for a in ASSETS}))
   if c['id'] in ['drop_2_g0_p0','drop_2_g120_p60','drop_1_g0_p0','drop_0.5_g0_p0','price_0.1_g0_p0'] or interval==1:
    examples[key]=dict(low=rowslow,high=rowshigh)
  print('done interval',interval,flush=True)
 save('actual_fast_results.json',all_results);save('actual_fast_coverage.json',coverage)
 np.savez_compressed(P/'actual_fast_arrays.npz',ids=np.array(ids),tickers=np.array([r['ticker'] for r in rr]),low=low,high=high,triggers=triggers)
 with gzip.open(P/'actual_fast_details.json.gz','wt') as f:json.dump(examples,f)
 for i in [1,10,30,60]:
  for kind in ['drop_2_g0_p0','drop_1_g0_p0','drop_2_g120_p60']:
   row=next(x for x in all_results if x['id']==f'i{i}_'+kind);print(row['id'],json.dumps({k:row['low'][k] for k in ['trades','losses_reduced','recovered','winners_cut','net','source_missing','quote_missing']}),'upper',row['high']['net'])
 print('missing',sum(r['missing_source']>0 for r in coverage),flush=True)
if __name__=='__main__':main()
