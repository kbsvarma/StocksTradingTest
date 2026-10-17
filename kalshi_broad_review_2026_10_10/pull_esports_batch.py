"""Equivalent frozen CS2 quote inputs, grouped into bounded bulk requests."""
from common import *
from collections import defaultdict
from datetime import datetime,timezone
series='KXCS2GAME';dest=P/'replays'/series;dest.mkdir(parents=True,exist_ok=True)
meta=get('/series/'+series)['series'];save(dest/'series.json',meta)
ms=paged('/markets',{'series_ticker':series,'status':'settled','min_close_ts':START,'max_close_ts':END,'limit':1000},'markets');ms=[m for m in ms if START<=ts(m['close_time'])<END];save(dest/'markets.json',ms)
events=defaultdict(list)
for m in ms:events[m['event_ticker']].append(m)
mi=paged('/milestones',{'category':'Esports','minimum_start_date':datetime.fromtimestamp(START-7*86400,timezone.utc).isoformat(),'limit':500},'milestones');mapping=defaultdict(list)
for m in mi:
 e=(m.get('details') or {}).get('main_game_event_ticker')
 if e in events:mapping[e].append(m)
save(dest/'batch_milestones.json',mi)
eligible=[];done=0
for e,items in events.items():
 if (dest/(e+'_candles.json.gz')).exists():continue
 matched=mapping[e]
 if len(matched)!=1:
  matched=get('/milestones',{'limit':100,'related_event_ticker':e})['milestones'];matched=[m for m in matched if (m.get('details') or {}).get('main_game_event_ticker')==e]
 save(dest/(e+'_milestones.json'),matched)
 if len(matched)!=1:save(dest/(e+'_skip.json'),{'reason':'ambiguous_or_missing_start','count':len(matched)});continue
 start=ts(matched[0]['start_date'])-3600;close=max(ts(m['close_time']) for m in items)
 # All frozen sports rules evaluate no later than start+6h and execute at +1m.
 end=min(close,ts(matched[0]['start_date'])+6*3600+60)
 eligible.append((e,items,start,end))
print('BULK CS2',len(eligible),'events',len(mi),'milestones',flush=True)
for j in range(0,len(eligible),10):
 group=sorted(eligible,key=lambda x:x[2])[j:j+10];markets=[m for e,items,lo,hi in group for m in items];lo=min(x[2] for x in group);end=max(x[3] for x in group);out=defaultdict(list);step=(8000//len(markets)-2)*60
 for lower in range(lo,end,step):
  upper=min(end,lower+step);x=get('/markets/candlesticks',{'market_tickers':','.join(m['ticker'] for m in markets),'start_ts':lower,'end_ts':upper,'period_interval':1})
  for row in x.get('markets',[]):out[row['market_ticker']]+=row['candlesticks']
 for e,items,lower,upper in group:
  body={m['ticker']:sorted({c['end_period_ts']:c for c in out.get(m['ticker'],[]) if lower<=c['end_period_ts']<=upper}.values(),key=lambda c:c['end_period_ts']) for m in items};save(dest/(e+'_candles.json.gz'),body)
 done+=len(group)
 if done%100==0:print('CS2',done,flush=True)
save(dest/'receipt.json',{'markets':len(ms),'events':len(events),'start':START,'end':END,'finished':time.time(),'method':'batch; complete frozen decision horizons with one-minute execution lookahead'})
print('DONE CS2',done,flush=True)
