"""Frozen GET-only candidate coverage. Public candle data; no credentials or orders."""
import json,datetime,time,sys
from pathlib import Path
from census import get
P=Path(__file__).resolve().parent;D=P/'candidate_data';D.mkdir(exist_ok=True)
def ts(s):return int(datetime.datetime.fromisoformat(s.replace('Z','+00:00')).timestamp())
END=ts('2026-09-26T00:00:00Z');START=ts('2026-09-08T00:00:00Z')
SHORT='KXGOLD15M KXWTI15M KXSILVER15M KXCOPPER15M KXNATGAS15M KXPALLADIUM15M KXPLATINUM15M KXEURUSD15M KXGBPUSD15M KXUSDJPY15M KXZEC15M KXXRP15M'.split()
OTHER='KXAAAGASD KXAAAGASW KXSPOTIFYD KXRT KXTSAW KXTRUTHSOCIAL KXRAIN KXHIGHNY KXTEMPMIAH KXTEMPNYCHS KXTEMPCHIHS KXTEMPLAXHS KXUST10AD'.split()
def run(t):
 start=START if '15M' in t or 'TEMP' in t or t in ['KXRAIN','KXHIGHNY'] else ts('2026-08-08T00:00:00Z')
 mp=D/(t+'_markets.json');cp=D/(t+'_candles.jsonl')
 if mp.exists():ms=json.loads(mp.read_text())
 else:
  ms=[];cursor='';seen=set()
  while True:
   q={'series_ticker':t,'status':'settled','min_settled_ts':start,'max_settled_ts':END,'limit':1000}
   if cursor:q['cursor']=cursor
   r=get('/markets',q);ms+=r['markets'];cursor=r.get('cursor','')
   if not cursor:break
   if cursor in seen:raise ValueError('cursor loop')
   seen.add(cursor)
  # Enforce close-time window too. Settlement lag must not introduce older observations.
  ms=[m for m in ms if start<=ts(m['close_time'])<END and m.get('result') in ['yes','no']]
  mp.write_text(json.dumps(ms))
 # Hourly weather sampled full predetermined recent week (all strikes); daily weather full window.
 # Hourly weather expanded to the full fixed September 8-25 window for all four cities.
 done={json.loads(l)['ticker'] for l in cp.read_text().splitlines()} if cp.exists() else set()
 todo=sorted([m for m in ms if m['ticker'] not in done],key=lambda m:m['close_time'])
 interval=1 if '15M' in t or 'TEMP' in t else 60
 lookback=20*60 if '15M' in t else (90*60 if 'TEMP' in t else 32*3600)
 groups=[];group=[]
 for m in todo:
  trial=group+[m];startq=min(max(ts(x['open_time']),ts(x['close_time'])-lookback) for x in trial);endq=max(ts(x['close_time']) for x in trial)
  estimate=len(trial)*((endq-startq)//(interval*60)+2)
  if group and (len(trial)>50 or estimate>8000):groups.append(group);group=[]
  group.append(m)
 if group:groups.append(group)
 print(t,'markets',len(ms),'remaining',len(todo),'batches',len(groups),flush=True)
 with cp.open('a') as f:
  for i,g in enumerate(groups):
   startq=min(max(ts(x['open_time']),ts(x['close_time'])-lookback) for x in g);endq=max(ts(x['close_time']) for x in g)
   q={'market_tickers':','.join(m['ticker'] for m in g),'start_ts':startq,'end_ts':endq,'period_interval':interval}
   r=get('/markets/candlesticks',q);got={x['market_ticker']:x['candlesticks'] for x in r.get('markets',[])}
   for m in g:
    cs=[]
    for c in got.get(m['ticker'],[]):cs.append({'t':c['end_period_ts'],'bo':c.get('yes_bid',{}).get('open_dollars'),'bc':c.get('yes_bid',{}).get('close_dollars'),'ao':c.get('yes_ask',{}).get('open_dollars'),'ac':c.get('yes_ask',{}).get('close_dollars'),'v':c.get('volume_fp')})
    f.write(json.dumps({'ticker':m['ticker'],'interval_minutes':interval,'candles':cs})+'\n')
   f.flush()
   if i%20==0:print(t,'batch',i+1,'/',len(groups),flush=True)
   time.sleep(.65)
 print(t,'DONE',flush=True)
if __name__=='__main__':
 for t in sys.argv[1:] or SHORT+OTHER:run(t)
