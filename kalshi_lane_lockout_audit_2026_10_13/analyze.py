"""Run saved production scanner against archived inputs; bypass books, never invent fills."""
import collections,copy,gzip,json,sys
from datetime import datetime
from decimal import Decimal as D
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo
P=Path(__file__).resolve().parent;sys.path.insert(0,str(P/'source'))
import market_data
from lanes import lane_for,Z2,Z3,ACTIVE_ASSETS
from price_rules import limit_price
from execution_retry import fresh_retry_candidate
ET=ZoneInfo('America/New_York');RETRIES_ACTIVATED=1791168942.0308564
def et(t):return datetime.fromtimestamp(t,ET).isoformat()
def load(n):return json.load(gzip.open(P/(n+'.json.gz'),'rt'))
def save(n,x):(P/n).write_text(json.dumps(x,indent=2)+'\n')
orders=load('orders');attempts=load('attempts');events=load('events');obs=load('observer')
om={r['ticker']:r for r in orders};archive=json.loads((P/'archive.json').read_text())
replays={}
for ticker,raw in archive.items():
 if 'error' in raw:replays[ticker]=raw;continue
 r=om[ticker];old=r['signal'];market=raw['market'];candles=raw['candles'];bars=raw['spot'];records=[]
 def get(url):
  if '/markets?' in url:return {'markets':[market]}
  if '/candlesticks?' in url:return candles
  if 'api.exchange.coinbase.com' in url:return bars
  raise AssertionError('Unrecorded endpoint '+url)
 for T in range(old['decision_ts'],r['close_ts']-179,60):
  for lane in (Z2,Z3):
   with patch.object(market_data,'get',side_effect=get),patch.object(market_data.time,'time',return_value=T+3),\
        patch.object(market_data,'refresh_book',side_effect=lambda s:dict(s,status='qualified_no_depth_evidence')):
    rec=market_data.scan(r['asset'],T,set(),lane=lane)
   rec['reconstructed_from_later_archive']=True;rec['depth_verified']=False;rec['first_arrival_verified']=False
   rec['same_side_as_zero']=rec.get('side')==r['side'];rec['et']=et(T)
   rec['lockout_was_logged']=any(e.get('ticker')==ticker and e.get('decision_ts')==T and e.get('lane',Z2)==lane and e.get('status') in ('already_entered','zero_fill_locked') for e in events)
   if rec.get('signal_qualified'):rec['unchanged_execution_cap']=str(limit_price(rec)[1])
   records.append(rec)
 original=next(x for x in records if x['decision_ts']==old['decision_ts'] and x['lane']==r['lane'])
 anchor={k:dict(live=old.get(k),archive=original.get(k),match=(abs(old[k]-original[k])<1e-9 if isinstance(old.get(k),(int,float)) and isinstance(original.get(k),(int,float)) else old.get(k)==original.get(k))) for k in ('side','signal_bid','signal_ask','z','spot','strike_proxy','sigma')}
 same=[x for x in records if x['decision_ts']>old['decision_ts'] and x['lane']==r['lane'] and x['same_side_as_zero'] and x.get('signal_qualified')]
 other=[x for x in records if x['decision_ts']>old['decision_ts'] and x['lane']!=r['lane'] and x.get('signal_qualified')]
 replays[ticker]=dict(anchor=anchor,anchor_all_match=all(x['match'] for x in anchor.values()),later_same_lane_qualified=len(same),later_other_lane_qualified=len(other),all_minutes=records,result=market.get('result'),later_actual_book_receipts=sum(o.get('ticker')==ticker and o.get('decision_ts',0)>old['decision_ts'] for o in obs),conclusion='Qualifying archived inputs do not establish contemporaneous feed availability, sufficient depth, risk clearance or a fill.')
save('replays.json',replays)

def depth(levels,cap,floor):
 qty=D(0);weak=False;best=None
 for price,n in sorted((1-D(p),D(n)) for p,n in levels):
  if n<=0:continue
  if best is None:best=price
  if price<floor:weak=True;break
  if price>cap:break
  qty+=min(5-qty,n)
  if qty==5:break
 return dict(enough=qty==5 and not weak,depth=str(qty),best=str(best),weak=weak)
checks={}
for r in attempts:
 if r['status']!='unfilled':continue
 ticker=r['ticker'];s=r['signal'];T=s['decision_ts'];lane=r.get('lane',Z2);seen=set();books=[];cap=D(r['favorite_limit']);floor=lane_for(r).floor
 def add(source,ts,levels):
  if (source,ts) in seen:return
  seen.add((source,ts));books.append(dict(source=source,ts=ts,et=et(ts),age=ts-T,after_zero=(ts>=(r.get('zero_fill_confirmation') or {}).get('ts',r['created_ts'])),inside25=0<=ts-T<=25,**depth(levels,cap,floor)))
 for e in events:
  if e.get('ticker')!=ticker or e.get('lane',Z2)!=lane or e.get('decision_ts')!=T:continue
  for field,tsfield in (('book_levels','book_observed_ts'),('execution_book_levels','execution_book_ts')):
   if field in e:add(field,e[tsfield],e[field])
 for o in obs:
  if o.get('ticker')==ticker and o.get('lane',Z2)==lane and o.get('decision_ts')==T and 'levels' in o:add('observer',o['observed_ts'],o['levels'])
 final=om[ticker];same=[b for b in books if b['after_zero'] and b['inside25']]
 checks[ticker]=dict(asset=r['asset'],lane=lane,zero_et=et(r['created_ts']),cap=str(cap),status=final['status'],recovered=bool(D(final['filled'])),recovery_seconds=final['created_ts']-r['created_ts'] if D(final['filled']) else None,after_zero_books_in25=len(same),after_zero_full5_in25=sum(b['enough'] for b in same),books=sorted(books,key=lambda x:x['ts']))
save('book_checks.json',checks)
table={}
for a in ACTIVE_ASSETS:
 os=[r for r in orders if r['asset']==a and r['created_ts']>=RETRIES_ACTIVATED];zs=[r for r in attempts if r['asset']==a and r['created_ts']>=RETRIES_ACTIVATED and r['status']=='unfilled']
 table[a]=dict(markets_submitted=len(os),filled_markets=sum(D(r['filled'])>0 for r in os),zero_attempts=len(zs),recovered_markets=sum(D(om[r['ticker']]['filled'])>0 for r in zs),still_unfilled=sum(r['status']=='unfilled' for r in os),zero_lanes=dict(collections.Counter(r.get('lane',Z2) for r in zs)))
control=copy.deepcopy(next(r for r in attempts if r['ticker']=='KXWTI15M-26OCT132115-15'));policy=[]
for a in ACTIVE_ASSETS:
 for lane in (Z2,Z3):
  r=copy.deepcopy(control);r.update(asset=a,ticker='KX'+a+'15M-POLICY',lane=lane);r['signal'].update(asset=a,ticker=r['ticker'],lane=lane)
  T=r['signal']['decision_ts']+60
  policy.append(dict(asset=a,lane=lane,allows_fresh_minute=fresh_retry_candidate(r,T,T+3,lane)))
save('analysis.json',dict(retry_period_start_et=et(RETRIES_ACTIVATED),by_asset=table,policy=policy,total_orders=len(orders),total_attempts=len(attempts),total_terminal_zero=sum(r['status']=='unfilled' for r in orders),all_zero_attempts=sum(r['status']=='unfilled' for r in attempts),no_broker_writes=True))
print(json.dumps(table,indent=2))
for t,r in replays.items():
 print(t,'anchor',r.get('anchor_all_match'),'later original lane',r.get('later_same_lane_qualified'),'later other lane',r.get('later_other_lane_qualified'))
 for s in r.get('all_minutes',[]):print(s['et'],s['lane'],s['status'],s.get('side'),s.get('signal_ask'),s.get('z'),s.get('unchanged_execution_cap'))
