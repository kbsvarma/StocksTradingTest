"""GET-only independent gap replay. Never imports execution engine or runner."""
import json,time,math,statistics,sqlite3,collections,urllib.request,urllib.parse
from pathlib import Path
from datetime import datetime
D=Path(__file__).resolve().parent/'gap_audit_2026_09_14';D.mkdir(exist_ok=True)
START=1789392900
END=int(time.time()//60)*60-60
BASE='https://api.elections.kalshi.com/trade-api/v2'
def fetch(url):
 import hashlib
 path=D/(hashlib.sha256(url.encode()).hexdigest()+'.json')
 if path.exists():return json.loads(path.read_text())
 for i in range(4):
  try:
   time.sleep(.22)
   with urllib.request.urlopen(urllib.request.Request(url,headers={'User-Agent':'kalshi-readonly-audit'}),timeout=10) as r:data=json.load(r)
   path.write_text(json.dumps(data));return data
  except Exception:
   if i==3:raise
   time.sleep(1+i)
def ts(s):return int(datetime.fromisoformat(s.replace('Z','+00:00')).timestamp())
c=sqlite3.connect('file:'+str(Path(__file__).resolve().parent/'state/orders.sqlite3')+'?mode=ro',uri=True)
log=collections.defaultdict(list)
for row in c.execute('select event from events where ts>=?',(START,)):
 r=json.loads(row[0]);log[(r.get('asset'),r.get('decision_ts'))].append(r)
results=[];diffs=[];missing=[];signals=[];rawchecks=0
for asset in ('BTC','ETH','SOL','XRP'):
 bars={}
 for lo in range(START-3660,END+1,240*60):
  data=fetch('https://api.exchange.coinbase.com/products/'+asset+'-USD/candles?'+urllib.parse.urlencode({'granularity':60,'start':lo,'end':min(lo+240*60,END)}))
  bars.update({int(x[0]):float(x[4]) for x in data})
 series='KX'+asset+'15M';markets=[];cursor=''
 while True:
  p={'series_ticker':series,'min_close_ts':START,'max_close_ts':END+900,'limit':1000}
  if cursor:p['cursor']=cursor
  data=fetch(BASE+'/markets?'+urllib.parse.urlencode(p));markets+=data['markets'];cursor=data.get('cursor') or ''
  if not cursor:break
 count=collections.Counter();maxz=-999
 for m in markets:
  op,cl=ts(m['open_time']),ts(m['close_time'])
  data=fetch(BASE+'/series/'+series+'/markets/'+m['ticker']+'/candlesticks?'+urllib.parse.urlencode({'start_ts':op,'end_ts':min(cl,END),'period_interval':1})) if op<=END else {'candlesticks':[]}
  candles={x['end_period_ts']:x for x in data.get('candlesticks',[])}
  for T in range(max(START,cl-600),min(END,cl-180)+1,60):
   rawchecks+=1;record={'asset':asset,'ticker':m['ticker'],'T':T};old=log.get((asset,T),[])
   if not old:missing.append(record)
   q=candles.get(T)
   if not q:count['missing_candle']+=1;continue
   try:b=float(q['yes_bid']['close_dollars']);a=float(q['yes_ask']['close_dollars'])
   except (KeyError,ValueError,TypeError):count['invalid_quote']+=1;continue
   side='yes' if a+b>1 else 'no';bid,ask=(b,a) if side=='yes' else (1-a,1-b)
   record.update(ask=ask,side=side)
   if old and old[0].get('signal_ask') is not None and abs(old[0]['signal_ask']-ask)>1e-7:diffs.append(dict(record,kind='quote',live=old[0]['signal_ask']))
   if not .85-1e-8<=ask<.93-1e-8 or ask-bid>.02+1e-8:count['price_gate']+=1;continue
   times=list(range(T-3660,T,60))
   if op-60 not in bars or any(t not in bars for t in times):count['missing_spot']+=1;continue
   prices=[bars[t] for t in times];sigma=statistics.pstdev([math.log(prices[i]/prices[i-1]) for i in range(1,61)])
   z=(1 if side=='yes' else -1)*(bars[T-60]-bars[op-60])/bars[op-60]/sigma/math.sqrt((cl-T)/60) if sigma>0 else 0
   record['z']=z;maxz=max(maxz,z)
   if old and old[0].get('z') is not None and abs(old[0]['z']-z)>1e-7:diffs.append(dict(record,kind='z',live=old[0]['z']))
   if z<2:count['cushion_gate']+=1;continue
   count['qualified_candle']+=1;record['live']=[{k:x.get(k) for k in ('status','execution_status')} for x in old];signals.append(record)
 results.append({'asset':asset,'markets':len(markets),'checks':dict(count),'max_in_band_z':maxz})
 print(json.dumps(results[-1]),flush=True)
out={'start':START,'end':END,'expected_entry_checks':rawchecks,'assets':results,'missing_live_checks':missing,'live_vs_refetched_differences':diffs,'qualified_signals':signals}
(D/'RESULT.json').write_text(json.dumps(out,indent=2));print('RESULT',json.dumps(out),flush=True)
