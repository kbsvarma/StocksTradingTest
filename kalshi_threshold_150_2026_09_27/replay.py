import json,sys,math,concurrent.futures,datetime,collections,re
from decimal import Decimal,ROUND_FLOOR,ROUND_CEILING
from pathlib import Path
from zoneinfo import ZoneInfo
P=Path(__file__).resolve().parent;D=P/'data';sys.path.insert(0,str(P.parent/'kalshi_full_review_2026_09_26'));from census import get
raw=json.loads((D/'recorded.json').read_text());ev=raw['events'];tickers=sorted({e['ticker'] for e in ev});ET=ZoneInfo('America/New_York')
def ts(s):
 s=re.sub(r'\.(\d+)',lambda m:'.'+m.group(1)[:6].ljust(6,'0'),s)
 return datetime.datetime.fromisoformat(s.replace('Z','+00:00')).timestamp()
def day(t):return datetime.datetime.fromtimestamp(t,ET).date().isoformat()
def fetch(t):
 f=D/(t+'.json')
 if f.exists():return
 m=get('/markets/'+t)['market'];start=int(ts(m['open_time']));end=int(ts(m['close_time']));c=get('/series/'+t.split('-')[0]+'/markets/'+t+'/candlesticks',{'start_ts':start,'end_ts':end,'period_interval':1});f.write_text(json.dumps({'market':m,'candles':c['candlesticks']}))
with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
 for i,_ in enumerate(pool.map(fetch,tickers)):
  if i%50==0:print('fetched',i,flush=True)
markets={t:json.loads((D/(t+'.json')).read_text()) for t in tickers}
def cap(e):
 d=Decimal;limit=min(d(str(e['signal_ask']))+d('.01'),d('.939'));y=limit if e['side']=='yes' else 1-limit
 for r in e['price_ranges']:
  lo,hi,step=map(d,(r['start'],r['end'],r['step']))
  if lo<=y<=hi:
   px=lo+((y-lo)/step).to_integral_value(rounding=ROUND_FLOOR if e['side']=='yes' else ROUND_CEILING)*step
   spend=px if e['side']=='yes' else 1-px
   if lo<=px<=hi and d('.85')<=spend<=limit:return float(spend)
 return None

def replay(z,start,mode):
 done=set();rows=[];skips=collections.Counter();pending=[];realized=collections.defaultdict(float)
 for e in sorted(ev,key=lambda e:(e['decision_ts'],e['observed_ts'])):
  T=e['decision_ts'];t=e['ticker']
  if T<start or e['z']<z or t in done:continue
  if not(3<=e['remaining_minutes']<=10 and .85-1e-8<=e['signal_ask']<.93-1e-8 and e['signal_ask']-e['signal_bid']<=.02000001):continue
  if e['observed_ts']-T>35:skips['stale_signal']+=1;continue
  md=markets[t];m=md['market']
  if m.get('result') not in ('yes','no'):skips['unsettled']+=1;continue
  limit=cap(e)
  if limit is None:skips['tick']+=1;continue
  if mode=='cap_cost':
   if e.get('status')=='book_outside_limit' or e.get('execution_status') in ('Insufficient depth inside cap','Order reconciliation pending'):
    skips['recorded_execution_block']+=1;continue
   actual=next((r for r in raw['orders'] if r['ticker']==t),None)
   if e.get('execution_status')=='ack' and actual and actual.get('status')=='unfilled':
    skips['recorded_unfilled']+=1;done.add(t);continue
   price=limit;entry_ts=T+3
  else:
   c=next((c for c in md['candles'] if c['end_period_ts']==T+60),None)
   try:price=float(c['yes_ask']['close_dollars']) if e['side']=='yes' else 1-float(c['yes_bid']['close_dollars'])
   except (KeyError,TypeError,ValueError):skips['quote_missing']+=1;continue
   price=max(price,e['signal_ask'])
   if not .85-1e-8<=price<=limit+1e-8:skips['next_quote_outside_cap']+=1;continue
   entry_ts=T+60
  for r in pending[:]:
   if r['settled_ts']<=entry_ts:realized[day(r['settled_ts'])]+=r['pnl'];pending.remove(r)
  if len(pending)>=4 or any(r['asset']==e['asset'] for r in pending):skips['exposure']+=1;continue
  if realized[day(entry_ts)]-(len(pending)+1)*1.90 < -10:skips['daily_budget']+=1;continue
  fee=math.ceil((.07*2*price*(1-price)-1e-12)*10000)/10000
  won=m['result']==e['side'];pnl=2*(int(won)-price)-fee
  settle=ts(m.get('settlement_ts') or m.get('settlement_time') or m['close_time'])
  if settle<entry_ts:raise ValueError('settlement chronology')
  row=dict(ticker=t,asset=e['asset'],side=e['side'],z=e['z'],entry_ts=entry_ts,settled_ts=settle,price=price,fee=fee,pnl=pnl,win=won)
  rows.append(row);pending.append(row);done.add(t)
 return {'threshold':z,'mode':mode,'trades':len(rows),'wins':sum(r['win'] for r in rows),'losses':sum(not r['win'] for r in rows),'pnl':sum(r['pnl'] for r in rows),'skips':dict(skips),'rows':rows}
results={}
for period,start in [('all',raw['first_event']),('last7days',raw['export_ts']-7*86400)]:
 results[period]={}
 for mode in ['cap_cost','next_minute']:
  rr=[replay(z,start,mode) for z in [2,1.5]];base={r['ticker']:r for r in rr[0]['rows']}
  for r in rr:
   extra=[x for x in r['rows'] if x['ticker'] not in base];r['extra_markets']={'trades':len(extra),'wins':sum(x['win'] for x in extra),'losses':sum(not x['win'] for x in extra),'pnl':sum(x['pnl'] for x in extra)};r['delta_pnl']=r['pnl']-rr[0]['pnl']
   print(period,mode,json.dumps({k:v for k,v in r.items() if k!='rows'}),flush=True)
  results[period][mode]=rr
(P/'results.json').write_text(json.dumps(results,indent=2))
