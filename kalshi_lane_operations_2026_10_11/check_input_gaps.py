"""Later historical source inspection, never point-in-time fill reconstruction."""
import json,gzip,time,requests,statistics,math,sys
from pathlib import Path
from datetime import datetime
P=Path(__file__).resolve().parent;sys.path.insert(0,str(P/'source'))
from lanes import lane_for
def stamp(s):return int(datetime.fromisoformat(s.replace('Z','+00:00')).timestamp())
markets=json.load(gzip.open(P/'markets.json.gz','rt'));inputs=json.loads((P/'input_failures.json').read_text());cases={}
for r in inputs:
 if r['last'] in ('incomplete_spot','decision_candle_unavailable'):cases.setdefault((r['asset'],r['ts']),[]).append(r)
session=requests.Session();cache={};base='https://api.elections.kalshi.com/trade-api/v2';out=[]
def get(path):
 if path in cache:return cache[path]
 sec=time.time()%60
 if sec<32:time.sleep(32-sec)
 t=time.time();r=session.get(base+path,timeout=6);r.raise_for_status();d=r.json();cache[path]=dict(fetched=time.time(),duration=time.time()-t,data=d);time.sleep(.3);return cache[path]
for (asset,T),rs in cases.items():
 e=rs[0]['trace'][0];t=e['ticker'];market=next(m for m in markets[asset] if m['ticker']==t);past=sorted((m for m in markets[asset] if stamp(m['open_time'])<=stamp(market['open_time'])),key=lambda m:m['open_time'])[-3:];bars={};receipts=[]
 for m in past:
  path='/live_data/events/'+m['event_ticker']+'?range=1h';r=get(path);receipts.append(path)
  for point in r['data'].get('live_data',{}).get('details',{}).get('timeseries',[]):
   if int(point['t'])%60000==59000:bars[int(point['t'])//60000*60]=float(point['v'])
 need=list(range(T-3660,T,60));missing=[k for k in need if k not in bars];start=stamp(market['open_time']);remaining=(stamp(market['close_time'])-T)/60
 quote=e
 if 'signal_ask' not in quote:
  path='/series/KX'+asset+'15M/markets/'+t+'/candlesticks?start_ts='+str(T-60)+'&end_ts='+str(T)+'&period_interval=1';r=get(path);receipts.append(path);c=next((c for c in r['data'].get('candlesticks',[]) if c['end_period_ts']==T),None)
  if c and c.get('yes_bid',{}).get('close_dollars') is not None and c.get('yes_ask',{}).get('close_dollars') is not None:
   b=float(c['yes_bid']['close_dollars']);a=float(c['yes_ask']['close_dollars']);side='yes' if a+b>1 else 'no';quote=dict(side=side,signal_bid=b if side=='yes' else 1-a,signal_ask=a if side=='yes' else 1-b)
 z=None
 if not missing and start-60 in bars and 'side' in quote:
  vals=[bars[k] for k in need];sigma=statistics.pstdev(math.log(y/x) for x,y in zip(vals,vals[1:]));z=(1 if quote['side']=='yes' else -1)*(vals[-1]/bars[start-60]-1)/(sigma*math.sqrt(remaining)) if sigma else None
 result=dict(asset=asset,ticker=t,T=T,et=rs[0]['et'],original_failure=rs[0]['last'],remaining=remaining,missing_bars=missing,reference_present=start-60 in bars,later_z=z,later_quote={k:quote.get(k) for k in ('signal_bid','signal_ask','side')},receipts=receipts,lanes={r['lane']:lane_for(dict(asset=asset,lane=r['lane'])).threshold(remaining) for r in rs},caveat='Fetched after the event; may include revisions. Cannot establish timely availability or a fill.')
 out.append(result);print(json.dumps(result),flush=True)
(P/'input_gap_later_check.json').write_text(json.dumps(out,indent=2)+'\n')
with gzip.open(P/'input_gap_later_receipts.json.gz','wt') as f:json.dump(cache,f)
