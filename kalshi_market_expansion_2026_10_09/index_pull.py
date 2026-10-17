"""Bounded public historical inputs for three hourly index adaptations."""
from pathlib import Path
import importlib.util,json,gzip,time
from concurrent.futures import ThreadPoolExecutor
P=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('helper',P/'extension/pull.py');h=importlib.util.module_from_spec(spec);spec.loader.exec_module(h)
D=P/'indices/data';D.mkdir(parents=True,exist_ok=True)
START=h.ts('2026-09-28T00:00:00Z');END=h.ts('2026-10-09T15:00:00Z')
def run(series):
 dest=D/series;dest.mkdir(exist_ok=True)
 market_path=dest/'markets.json'
 if market_path.exists():rows=json.loads(market_path.read_text())
 else:
  cursor='';rows=[]
  while True:
   data=h.get('/markets',dict(series_ticker=series,status='settled',min_close_ts=START,max_close_ts=END,limit=1000,**({'cursor':cursor} if cursor else {})))
   rows.extend(m for m in data['response']['markets'] if START<=h.ts(m['close_time'])<END)
   cursor=data['response'].get('cursor')
   if not cursor:break
  h.write(market_path,rows)
 selected=[m for m in rows if float(m.get('volume_fp') or 0)>0 and m.get('strike_type') in ('greater','greater_or_equal') and m.get('floor_strike') is not None]
 print(series,'markets',len(rows),'positive_volume_thresholds',len(selected),flush=True)
 events={}
 for m in selected:events.setdefault(m['event_ticker'],[]).append(m)
 for i,(event,ms) in enumerate(sorted(events.items())):
  chart=dest/(event+'_chart.json.gz')
  if not chart.exists():
   raw=h.get('/live_data/events/'+event,dict(range='1h'))
   with gzip.open(chart,'wt') as f:json.dump(raw,f)
  quotes=dest/(event+'_quotes.json')
  if not quotes.exists():
   close=h.ts(ms[0]['close_time']);out=[]
   for j in range(0,len(ms),50):
    group=ms[j:j+50];raw=h.get('/markets/candlesticks',dict(market_tickers=','.join(m['ticker'] for m in group),start_ts=close-12*60,end_ts=close,period_interval=1));out.extend(raw['response']['markets'])
   h.write(quotes,out)
  if i%10==0:print(series,'events',i+1,'/',len(events),flush=True)
 return dict(series=series,markets=len(rows),positive_volume_thresholds=len(selected),events=len(events))
if __name__=='__main__':
 with ThreadPoolExecutor(max_workers=3) as pool:results=list(pool.map(run,('KXINXU','KXNASDAQ100U','KXDJI')))
 h.write(D/'receipt.json',dict(start=START,end=END,results=results,finished=time.time(),public_get_only=True))
