"""Public GET-only execution audit; reads production DB with mode=ro.
Never imports credentials, engine, or order client; never submits orders.
"""
import json,sqlite3,time,math,statistics,urllib.parse
from pathlib import Path
import market_data
from lanes import lane_for
from price_rules import limit_price
from io_budget import get_budget
from decimal import Decimal, ROUND_FLOOR, ROUND_CEILING
ROOT=Path('/home/varma/kalshi_execution_retry_2026_10_02/observer_state')
DB='file:/home/varma/kalshi_revalidation_2026_09_12/live_v1/state/orders.sqlite3?mode=ro'

def append(row):
 with (ROOT/'observations.jsonl').open('a') as f:f.write(json.dumps(row)+'\n')
def book_stats(levels,cap,n,floor=.85):
 need=n;cost=0;low=False
 for px,qty in sorted((1-float(p),float(q)) for p,q in levels):
  if qty<=0:continue
  if px<floor-1e-8:low=True
  if px>cap+1e-8:break
  take=min(need,qty);cost+=take*px;need-=take
  if need<=1e-9:break
 return dict(enough=need<=1e-9 and not low,filled_depth=n-need,average=cost/(n-need) if n>need else None,below_floor=low)
def exact_cap(s):return float(limit_price(s)[1])
def fresh_z(s):
 T=s['decision_ts'];start=s['close_ts']-900;a=s['asset']
 raw=market_data.get('https://api.exchange.coinbase.com/products/'+a+'-USD/candles?'+urllib.parse.urlencode(dict(granularity=60,start=T-95*60,end=T)))
 bars={int(r[0]):float(r[4]) for r in raw if int(r[0])<=T-60};keys=list(range(T-3660,T,60))
 if start-60 not in bars or not all(k in bars for k in keys):return None
 v=[bars[k] for k in keys];sigma=statistics.pstdev(math.log(y/x) for x,y in zip(v,v[1:]))
 return (1 if s['side']=='yes' else -1)*(v[-1]/bars[start-60]-1)/(sigma*math.sqrt(s['remaining_minutes'])) if sigma>0 else None

def main():
 ROOT.mkdir(exist_ok=True)
 c=sqlite3.connect(DB,uri=True,timeout=2);last=c.execute('select coalesce(max(id),0) from events').fetchone()[0]
 pending={};seen=set();total=0;error=''
 while True:
  try:
   for ident,raw in c.execute('select id,event from events where id>? order by id',(last,)).fetchall():
    last=ident;s=json.loads(raw);T=s.get('decision_ts',0);key=(s.get('lane','cushion_z2'),s.get('ticker'),T)
    if key in seen or time.time()-T>25 or not isinstance(s.get('z'),(int,float)) or s['z']<1.8:continue
    if not all(k in s for k in ('ticker','side','signal_ask','remaining_minutes','asset')):continue
    s=dict(s);s.setdefault('close_ts',T+s['remaining_minutes']*60)
    seen.add(key);pending[key]=dict(signal=s,checks=0,next=time.time(),revised=False)
   books={}
   for key,job in list(pending.items()):
    s=job['signal'];age=time.time()-s['decision_ts']
    if age>35:pending.pop(key);continue
    if time.time()<job['next']:continue
    try:
     if s['ticker'] not in books:
      with get_budget(2):book=market_data.get(market_data.BASE+'/markets/'+s['ticker']+'/orderbook?depth=10').get('orderbook_fp',{})
      books[s['ticker']]=(book,time.time())
     book,received=books[s['ticker']]
     levels=book.get('no_dollars' if s['side']=='yes' else 'yes_dollars') or []
     cap=exact_cap(s)
     row=dict(mode='GET_ONLY',diagnostics_version=2,lane=s.get('lane','cushion_z2'),floor=str(lane_for(s).floor),observed_ts=received,decision_ts=s['decision_ts'],ticker=s['ticker'],original_z=s['z'],original_status=s['status'],original_execution=s.get('execution_status'),cap=cap,levels=levels,depth={str(n):book_stats(levels,cap,n,float(lane_for(s).floor)) for n in (1,2,5)})
     if age>=20 and not job['revised']:
      row['later_coinbase_z']=fresh_z(s);row['later_coinbase_received_ts']=time.time();job['revised']=True
     append(row);total+=1;job['checks']+=1;job['next']=time.time()+3
     if job['checks']>=8:pending.pop(key)
    except Exception as exc:
     append(dict(mode='GET_ONLY',lane=s.get('lane','cushion_z2'),ticker=s['ticker'],ts=time.time(),error=str(exc)[:200]))
     job['checks']+=1;job['next']=time.time()+3
     if job['checks']>=8:pending.pop(key)
   # Bound deduplication memory to active minute and prior minute.
   seen={k for k in seen if time.time()-k[2]<120}
   error=''
  except Exception as e:error=str(e)[:300];append(dict(mode='GET_ONLY',ts=time.time(),error=error))
  tmp=ROOT/'status.tmp';tmp.write_text(json.dumps(dict(ts=time.time(),mode='GET_ONLY',diagnostics_version=2,observations=total,pending=len(pending),error=error)));tmp.replace(ROOT/'status.json')
  time.sleep(.5)
if __name__=='__main__':main()
