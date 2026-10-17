"""Bounded extended-history public GET archive; no private or trading endpoints."""
from pathlib import Path
import sys,time,shutil,json
from collections import defaultdict,Counter
P=Path(__file__).resolve().parent;OLD=P.parent/'kalshi_broad_review_2026_10_10'
sys.path.insert(0,str(OLD))
import common
common.P=P;common.RAW=P/'raw';common.RAW.mkdir(exist_ok=True)
from common import get,paged,save,read,ts
END=ts('2026-10-10T04:00:00Z')
CITIES={'KXTEMPMIAH':('miami',ts('2026-08-15T04:00:00Z')),'KXTEMPCHIHS':('chicago',ts('2026-09-05T04:00:00Z')),'KXTEMPLAXHS':('la-coastal',ts('2026-09-05T04:00:00Z'))}

def pull(series):
 city,start=CITIES[series];d=P/'data'/series;d.mkdir(parents=True,exist_ok=True)
 meta=get('/series/'+series)['series'];save(d/'series.json',meta)
 ms=paged('/markets',{'series_ticker':series,'min_close_ts':start,'max_close_ts':END,'limit':1000},'markets')
 ms=[m for m in ms if start<=ts(m['close_time'])<END];save(d/'markets.json',ms)
 events=defaultdict(list)
 for m in ms:events[m['event_ticker']].append(m)
 print('MARKETS',series,len(ms),len(events),Counter(m['status'] for m in ms),flush=True)
 bars={};conflicts=set();source_receipts=[]
 for lo in range(start,END,86400):
  path=d/f'source_{lo}.json.gz'
  if not path.exists():save(path,get('/live_data/weather/'+city,{'from':(lo-7200)*1000,'to':min(END,lo+86400)*1000}))
  x=read(path);points=x.get('timeseries',[])
  source_receipts.append({'start':lo,'n':len(points),'first':points[0]['t'] if points else None,'last':points[-1]['t'] if points else None})
  for a in points:
   t=int(a['t'])//1000
   if a.get('v') is None or t%60:continue
   v=(float(a['v']),a.get('status'))
   if t in bars and bars[t]!=v:conflicts.add(t)
   bars[t]=v
 for t in conflicts:bars.pop(t,None)
 save(d/'bars.json.gz',bars);save(d/'source_receipts.json',source_receipts)
 print('SOURCE',series,len(bars),'range',min(bars) if bars else None,max(bars) if bars else None,'conflicts',len(conflicts),flush=True)
 eligible=[];counts=Counter()
 for event,items in sorted(events.items(),key=lambda x:x[1][0]['close_time']):
  cl=ts(items[0]['close_time']);cp=d/(event+'_candles.json.gz');old=OLD/'replays'/series/cp.name
  if cp.exists():counts['existing']+=1;continue
  if old.exists():shutil.copyfile(old,cp);counts['reused']+=1;continue
  if not any(t in bars for t in range(cl-3600,cl+1,60)):
   counts['no_terminal_source']+=1;continue
  if any(m.get('result') not in ('yes','no') for m in items):counts['nonbinary']+=1;continue
  eligible.append((event,items,cl-41*60,cl))
 for j in range(0,len(eligible),4):
  group=eligible[j:j+4];markets=[m for e,items,lo,hi in group for m in items]
  lo=min(x[2] for x in group);end=max(x[3] for x in group);step=(8000//len(markets)-2)*60;out=defaultdict(list)
  for lower in range(lo,end,step):
   upper=min(end,lower+step);x=get('/markets/candlesticks',{'market_tickers':','.join(m['ticker'] for m in markets),'start_ts':lower,'end_ts':upper,'period_interval':1})
   for r in x.get('markets',[]):out[r['market_ticker']]+=r['candlesticks']
  for event,items,lower,upper in group:
   body={m['ticker']:sorted({c['end_period_ts']:c for c in out.get(m['ticker'],[]) if lower<=c['end_period_ts']<=upper}.values(),key=lambda c:c['end_period_ts']) for m in items}
   save(d/(event+'_candles.json.gz'),body);counts['pulled']+=1
  if j%100==0:print('CANDLES',series,j+len(group),'/',len(eligible),flush=True)
 receipt={'start':start,'end':END,'markets':len(ms),'events':len(events),'source_points':len(bars),'source_conflicts':len(conflicts),'source_first':min(bars) if bars else None,'source_last':max(bars) if bars else None,'counts':dict(counts),'finished':time.time()}
 save(d/'receipt.json',receipt);print('DONE',series,receipt,flush=True)

if __name__=='__main__':
 for series in sys.argv[1:] or CITIES:pull(series)
