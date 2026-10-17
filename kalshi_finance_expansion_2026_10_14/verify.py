"""Independent ledger arithmetic, causal timestamps, splits and frequency audit."""
from common import *
import engine as e
import math,pickle
from collections import Counter,defaultdict
import numpy as np
fx=read(P/'fx/results.json');fl=read(P/'fx/ledgers.json.gz');idx=read(P/'indices_results.json');il=read(P/'indices_ledgers.json.gz')
reproduced=read(P/'fx/reproduced_prior_convention/results.json')
checks=Counter();stats={};prior=read(P.parent/'kalshi_currency_review_2026_10_12/results.json');controls=[]
for prof,scenarios in fl.items():
 for label,assets in scenarios.items():
  stress=.02 if label=='quote2c' else .01
  for asset,periods in assets.items():
   for period,ll in periods.items():
    assert len(ll)==len({r['ticker'] for r in ll})
    for r in ll:
     assert r['t']<r['entered']<r['close']<=r['settled'];assert r['quantity']==5
     assert r['floor']-1e-8<=r['entry']<=r['cap']+1e-8;assert abs(r['fee']-e.fee(r['entry']))<1e-8
     assert abs(r['net']-(5*(r['won']-r['entry'])-r['fee']-5*stress))<1e-8
     checks['fx_rows']+=1
    assert abs(sum(r['net'] for r in ll)-fx[prof][label][asset][period]['net'])<1e-5
   new=reproduced[prof][label][asset]['prior'];old=prior[prof][label][asset]['full']
   controls.append(dict(profile=prof,scenario=label,asset=asset,match=all(abs(new[k]-old[k])<1e-5 for k in ['trades','wins','losses','net']),new={k:new[k] for k in ['trades','wins','losses','net']},old={k:old[k] for k in ['trades','wins','losses','net']}))
for prof,scenarios in il.items():
 for label,assets in scenarios.items():
  stress=.02 if label=='quote2c' else .01
  for asset,ll in assets.items():
   assert len(ll)==len({(r['asset'],r['close']) for r in ll})
   for r in ll:
    assert r['source_first_ts']<r['source_ts']<r['t']<r['entered']<r['close']<=r['settled']<=END
    assert r['quantity']==5 and r['entry']<=r['cap']+1e-8
    assert abs(r['fee']-e.fee(r['entry'],r['multiplier']))<1e-8
    assert abs(r['net']-(5*(r['won']-r['entry'])-r['fee']-5*stress))<1e-8
    checks['index_rows']+=1
   assert abs(sum(r['net'] for r in ll)-idx[prof][label][asset]['net'])<1e-5

def uncertainty(ll,start,end):
 # Exploratory day-block interval, not adjusted for repeated research/model choice.
 days=[];t=start
 while t<end:
  d=e.day(t)
  if d not in days:days.append(d)
  t+=3600
 arr=np.array([sum(r['net'] for r in ll if e.day(r['entered'])==d) for d in days]);rng=np.random.default_rng(20261014);res=arr[rng.integers(0,len(arr),size=(20000,len(arr)))].sum(1)
 n=len(ll);w=sum(r['won'] for r in ll);p=w/n if n else 0;z=1.95996398454;den=1+z*z/n if n else 1;mid=(p+z*z/(2*n))/den if n else 0;rad=z*math.sqrt(p*(1-p)/n+z*z/(4*n*n))/den if n else 0
 return dict(calendar_dates=len(days),day_block_net95=list(map(float,np.quantile(res,[.025,.975]))),wilson_win95=[mid-rad,mid+rad],breakeven=e.metrics(ll)['breakeven'],warning='Exploratory; serial/cross-asset dependence and repeated model selection; no-loss samples cannot represent unobserved tail losses.')
for a in ['EURUSD','USDJPY','GBPUSD','AUDUSD','USDCAD']:
 ms=read(P/'fx/data'/('markets_'+a+'.json'));lo=min(ts(m['open_time']) for m in ms);days=Counter(e.day(ts(m['close_time'])-1) for m in ms);full=[d for d in days if e.day(lo)<d<e.day(END) and dt.date.fromisoformat(d).weekday()<5]
 stats[a]=dict(markets=len(ms),start=lo,end=END,calendar_elapsed_days=(END-lo)/86400,market_days=dict(days),complete_days=full,profiles={})
 for prof in fl:
  ll=fl[prof]['quote1c'][a]['full'];m=e.metrics(ll);stats[a]['profiles'][prof]=dict(trades_per_calendar_day=len(ll)/((END-lo)/86400),trades_per_full_day=sum(e.day(r['entered']) in full for r in ll)/len(full) if full else None,uncertainty=uncertainty(ll,lo,END),entry_daily_trades=m['daily_trades'])
ll=fl['eurusd_adaptive']['quote1c']['AUDUSD']['full']+fl['eurusd_adaptive']['quote1c']['USDCAD']['full']
count=Counter(r['close'] for r in ll);stats['AUD_CAD_dependence']=dict(same_expiry=sum(n>1 for n in count.values()),same_USD_direction=sum(next(x for x in ll if x['close']==t and x['asset']=='AUDUSD')['side']==-next(x for x in ll if x['close']==t and x['asset']=='USDCAD')['side'] for t,n in count.items() if n>1))
save(P/'validation.json',dict(passed=True,checks=dict(checks),control_note='40 controls reproduce the original convention before adding the stricter worse-quote cap rejection. Primary results use the stricter convention; original outputs retained under fx/reproduced_prior_convention.',prior_controls=controls,prior_all_match=all(x['match'] for x in controls)))
save(P/'frequency_uncertainty.json',stats)
print('VALIDATED',dict(checks),'prior controls',sum(x['match'] for x in controls),'/',len(controls))
for x in controls:
 if not x['match']:print('CONTROL_DIFFERENCE',x)
