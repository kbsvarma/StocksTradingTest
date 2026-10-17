"""Read-only offline, paired subminute exits. No trading client imported."""
from pathlib import Path
import math,json,gzip,pickle,bisect,statistics
from decimal import Decimal as D,ROUND_CEILING,ROUND_FLOOR
P=Path(__file__).resolve().parent
def sale(levels,quantity):
 left=D(str(quantity));gross=D(0);fee=D(0);used=[]
 for pi,qi in levels:
  p=D(pi)/10000;q=min(left,D(qi)/100)
  if p<=0 or q<=0:continue
  gross+=p*q;fee+=(D('.07')*q*p*(1-p)).quantize(D('.000001'),rounding=ROUND_CEILING);used.append([float(p),float(q)]);left-=q
  if left==0:break
 if left>0:return None
 net=(gross-fee).quantize(D('.0001'),rounding=ROUND_FLOOR)
 return dict(proceeds=float(net),gross=float(gross),fee=float(gross-net),levels=used)
def prepare(r,b,prices):
 out=dict(r,b=b,bt=[x[0] for x in b['books']],ct={k:[x[0] for x in v] for k,v in b['cf'].items()},z={},anchors={})
 for key,rows in b['cf'].items():
  j=bisect.bisect_right(out['ct'][key],r['t']*1000)-1
  assert j>=0 and 0<=r['t']*1000-rows[j][1]<=1500,(r['ticker'],key,'anchor_missing')
  out['anchors'][key]=rows[j][2]
 # All source observations used are known by the check timestamp. Historical
 # Coinbase bars supply only the rolling-vol/control sensitivity, not fast prices.
 for t in range(math.floor(r['entered'])+1,int(r['close'])):
  rem=(r['close']-t)/60;sig=r['entry_sigma'];minute=t//60*60;v=[prices.get(k) for k in range(minute-3660,minute,60)]
  rolling=statistics.pstdev(math.log(y/x) for x,y in zip(v,v[1:])) if all(v) else None
  z={}
  for key,rows in b['cf'].items():
   j=bisect.bisect_right(out['ct'][key],t*1000)-1
   if j<0 or not 0<=t*1000-rows[j][1]<=1500:continue
   spot=r['entry_spot']*rows[j][2]/out['anchors'][key]
   move=r['side']*(spot/r['reference']-1)
   z[key]=move/(sig*math.sqrt(rem))
   if key=='5hz':
    z['direct']=r['side']*(rows[j][2]/r['reference']-1)/(sig*math.sqrt(rem))
    if rolling:z['rolling']=move/(rolling*math.sqrt(rem))
  if rolling:
   z['minute']=r['side']*(v[-1]/r['reference']-1)/(rolling*math.sqrt((r['close']-minute)/60))
  out['z'][t]=z
 return out
def simulate(r,interval,drop,grace,persistence,feed='5hz',latency=1,friction=.01,phase=0):
 out={k:r[k] for k in ('ticker','asset','lane','won','entry_z','entered','close','quantity','cost','entry_fee','entry_friction','hold_net')}
 out.update(exit=False,net=r['hold_net'],delta=0,missed_source=0,missed_book=0,missed_depth=0)
 start=None;stop=r['entry_z']-drop;b=r['b'];bt=r['bt'];books=b['books'];sideidx=3 if r['side']==1 else 4
 for t in range((math.floor(r['entered']-phase)//interval+1)*interval+phase,int(r['close']),interval):
  if t-r['entered']<grace:continue
  z=r['z'].get(t,{}).get(feed)
  if z is None:start=None;out['missed_source']+=1;continue
  if z>=stop:start=None;continue
  if start is None:start=t
  if t-start<persistence:continue
  before=bisect.bisect_right(bt,t*1000)-1;after=bisect.bisect_left(bt,(t+latency)*1000)
  if before<0 or after>=len(books) or t*1000-bt[before]>500 or bt[after]-(t+latency)*1000>500 or bt[after]>=r['close']*1000:
   out['missed_book']+=1;continue
  a=sale(books[before][sideidx],r['quantity']);c=sale(books[after][sideidx],r['quantity'])
  if a is None or c is None:out['missed_depth']+=1;continue
  execution=min([a,c],key=lambda x:x['proceeds']);cash=execution['proceeds']-r['quantity']*friction
  net=cash-r['cost']-r['entry_fee']-r['entry_friction']
  out.update(exit=True,net=net,delta=net-r['hold_net'],trigger=t,trigger_z=z,stop_z=stop,breach_start=start,
    book_before=bt[before]/1000,book_after=bt[after]/1000,execution=execution,prebook=a,postbook=c,friction=r['quantity']*friction)
  return out
 return out
def metrics(rows):
 losers=[r for r in rows if not r['won']];winners=[r for r in rows if r['won']]
 return dict(n=len(rows),hold=sum(r['hold_net'] for r in rows),net=sum(r['net'] for r in rows),delta=sum(r['delta'] for r in rows),
  losses=len(losers),losses_reduced=sum(r['delta']>1e-8 for r in losers),losses_fully_avoided=sum(r['net']>=0 for r in losers),
  loss_savings=sum(r['delta'] for r in losers),winners=len(winners),winners_cut=sum(r['exit'] for r in winners),winners_turned_loss=sum(r['net']<0 for r in winners),
  winner_cost=-sum(r['delta'] for r in winners),exit_fees=sum(r.get('execution',{}).get('fee',0) for r in rows),
  exits=sum(r['exit'] for r in rows),missed_source=sum(r['missed_source'] for r in rows),missed_book=sum(r['missed_book'] for r in rows),missed_depth=sum(r['missed_depth'] for r in rows))
def main():
 raw=pickle.load(gzip.open(P/'inputs.pkl.gz'));results={};ledgers={};sens={};quality={}
 for name,cohort in raw['cohorts'].items():
  rr=[prepare(r,raw['bundles'][r['ticker']],raw['prices'][r['asset']]) for r in cohort]
  results[name]=[];ledgers[name]={};sens[name]={}
  for interval in (1,10,30,60):
   for drop in (.5,1,1.5,2):
    for grace in (0,30,60,120):
     for persistence in (0,10,30,60):
      key=f'i{interval}_d{drop:g}_g{grace}_p{persistence}';rows=[simulate(r,interval,drop,grace,persistence) for r in rr]
      results[name].append(dict(id=key,interval=interval,drop=drop,grace=grace,persistence=persistence,**metrics(rows)));ledgers[name][key]=rows
   for drop,grace,persistence in [(1,0,0),(2,0,0),(2,120,60)]:
    for feed,latency,friction,label in [('1hz',1,.01,'1hz'),('direct',1,.01,'direct_cf'),('rolling',1,.01,'rolling_sigma'),('minute',1,.01,'minute_control'),('5hz',.25,.01,'latency250ms'),('5hz',3,.01,'latency3s'),('5hz',1,.02,'friction2c'),('5hz',1,.05,'friction5c')]:
     key=f'i{interval}_d{drop:g}_g{grace}_p{persistence}_{label}';rows=[simulate(r,interval,drop,grace,persistence,feed,latency,friction) for r in rr];sens[name][key]=dict(metrics=metrics(rows),ledger=rows)
  quality[name]=[]
  for r in rr:
   cc=r['b']['cf']['5hz'];ct=[z for z in cc if r['entered']*1000<=z[0]<r['close']*1000];lag=sorted(z[0]-z[1] for z in ct);gaps=[y[0]-x[0] for x,y in zip(ct,ct[1:])]
   quality[name].append(dict(ticker=r['ticker'],observations=len(ct),median_lag_ms=statistics.median(lag),p99_lag_ms=lag[int(.99*(len(lag)-1))],max_receive_gap_ms=max(gaps),anchor=r['anchors'],source_seconds=sum('5hz' in z for z in r['z'].values()),total_seconds=len(r['z'])))
 (P/'results.json').write_text(json.dumps(results,indent=2)+'\n');(P/'sensitivity.json').write_text(json.dumps(sens,indent=2)+'\n');(P/'quality.json').write_text(json.dumps(quality,indent=2)+'\n')
 with gzip.open(P/'ledgers.json.gz','wt') as f:json.dump(ledgers,f)
 for name,rr in results.items():
  for r in rr:
   if (r['drop'],r['grace'],r['persistence']) in [(1,0,0),(2,0,0),(2,120,60)]:print(name,json.dumps(r),flush=True)
  print('EXPLORATORY BEST',name,sorted(rr,key=lambda x:x['net'],reverse=True)[:3],flush=True)
if __name__=='__main__':main()
