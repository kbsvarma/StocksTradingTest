"""Fetch predetermined weather and sports inputs, without trading APIs."""
import sys,json,time
from collections import defaultdict
from common import *
SPORTS='KXWTAMATCH KXATPMATCH KXNFLGAME KXNCAAFGAME KXNBAGAME KXWNBAGAME KXNHLGAME KXMLBGAME KXEPLGAME KXCS2GAME'.split()
WEATHER='KXTEMPNYCHS KXTEMPMIAH KXTEMPLAXHS KXTEMPCHIHS'.split()
def pull(t):
 dest=P/'replays'/t;dest.mkdir(parents=True,exist_ok=True)
 meta=get('/series/'+t)['series'];save(dest/'series.json',meta)
 markets=paged('/markets',{'series_ticker':t,'status':'settled','min_close_ts':START,'max_close_ts':END,'limit':1000},'markets')
 # Retain refunds for denominator; directional replay will not treat them as losses.
 markets=[m for m in markets if START<=ts(m['close_time'])<END]
 save(dest/'markets.json',markets)
 events=defaultdict(list)
 for m in markets:events[m['event_ticker']].append(m)
 done=0
 for et,ms in sorted(events.items()):
  cp=dest/(et+'_candles.json.gz')
  if cp.exists():continue
  close=max(ts(m['close_time']) for m in ms)
  if t in SPORTS:
   milestones=get('/milestones',{'limit':100,'related_event_ticker':et})['milestones'];save(dest/(et+'_milestones.json'),milestones)
   main=[x for x in milestones if (x.get('details') or {}).get('main_game_event_ticker')==et]
   if not main:main=[x for x in milestones if et in x.get('primary_event_tickers',[])]
   if len(main)!=1:save(dest/(et+'_skip.json'),{'reason':'ambiguous_or_missing_start','count':len(main)});continue
   mile=main[0];start=ts(mile['start_date'])-3600
   if t=='KXWTAMATCH':
    try:save(dest/(et+'_plays.json.gz'),get('/live_data/milestone/'+mile['id']+'/game_stats'))
    except Exception as e:save(dest/(et+'_plays_error.json'),{'error':str(e)})
  else:start=close-90*60
  start=max(start,min(ts(m['open_time']) for m in ms))
  out=defaultdict(list)
  # Split time and ticker dimensions, keeping the request below 10,000 candles.
  for j in range(0,len(ms),20):
   sub=ms[j:j+20];step=max(60,8000//len(sub)-2)*60
   for lo in range(start,close+1,step):
    hi=min(close,lo+step)
    if hi<=lo:continue
    x=get('/markets/candlesticks',{'market_tickers':','.join(m['ticker'] for m in sub),'start_ts':lo,'end_ts':hi,'period_interval':1})
    for row in x.get('markets',[]):out[row['market_ticker']]+=row['candlesticks']
  out={ticker:sorted({c['end_period_ts']:c for c in cs}.values(),key=lambda c:c['end_period_ts']) for ticker,cs in out.items()}
  save(cp,out);done+=1
  if done%25==0:print(t,done,'of',len(events),flush=True)
 save(dest/'receipt.json',{'markets':len(markets),'events':len(events),'start':START,'end':END,'finished':time.time()})
 print('DONE',t,len(markets),len(events),flush=True)
if __name__=='__main__':
 for t in sys.argv[1:] or WEATHER:
  try:pull(t)
  except Exception as e:print('ERROR',t,str(e),flush=True);save(P/'replays'/t/'error.json',{'error':str(e)})
