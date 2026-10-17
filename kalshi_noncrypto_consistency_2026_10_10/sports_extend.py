from common import *
from study import quote,execute,record,SCENARIOS,day
from collections import defaultdict
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
START_OLD=ts('2026-07-01T04:00:00Z');SPLIT_OLD=ts('2026-08-15T04:00:00Z')
D=P/'sports_earlier';D.mkdir(exist_ok=True)
OLD=P.parent/'research_data/StocksTradingTest/kalshi_sports_2026_08/data'

def pull(series,typ):
 d=D/series;d.mkdir(exist_ok=True);mp=d/'markets.json'
 if not mp.exists():
  current=paged('/markets',{'series_ticker':series,'min_close_ts':START_OLD,'max_close_ts':SPLIT_OLD,'limit':1000},'markets');historical=paged('/historical/markets',{'series_ticker':series,'limit':1000},'markets')
  ms={m['ticker']:m for m in historical+current if START_OLD<=ts(m['close_time'])<SPLIT_OLD};save(mp,list(ms.values()))
 ms=read(mp);known={m['ticker'] for m in ms}
 for m in read(P/'sports'/series/'markets.json'):
  mf=P/'sports'/series/(m['event_ticker']+'_milestone.json')
  if m['ticker'] not in known and mf.exists() and START_OLD<=ts(read(mf)['start_date'])<SPLIT_OLD:ms.append(m);known.add(m['ticker'])
 save(mp,ms);events=defaultdict(list)
 for m in ms:events[m['event_ticker']].append(m)
 ml=d/'milestones.json'
 if not ml.exists():save(ml,paged('/milestones',{'type':typ,'minimum_start_date':'2026-06-30T00:00:00Z','limit':500},'milestones'))
 by={m.get('details',{}).get('main_game_event_ticker'):m for m in read(ml)}
 archived={r['ticker']:r['candles'] for r in map(json.loads,(OLD/('games_'+series+'.jsonl')).read_text().splitlines())};missing=[];count=0
 for et,items in sorted(events.items()):
  mile=by.get(et)
  if not mile:missing.append({'event':et,'reason':'missing_start'});continue
  st=ts(mile['start_date'])
  if not START_OLD<=st<SPLIT_OLD:continue
  save(d/(et+'_milestone.json'),mile);dest=d/(et+'_candles.json.gz')
  if dest.exists():continue
  t=((st-1800)//60)*60;out={}
  for m in items:
   cs=[c for c in archived.get(m['ticker'],[]) if t-600<=c['end_period_ts']<=t+120]
   if not any(c['end_period_ts']==t for c in cs):
    if ts(m['settlement_ts'])<ts('2026-08-11T00:00:00Z'):
     raw=get('/historical/markets/'+m['ticker']+'/candlesticks',{'start_ts':t-660,'end_ts':t+120,'period_interval':1});cs=raw.get('candlesticks',[])
     for c in cs:
      for field in ('yes_bid','yes_ask'):
       for phase in ('open','close'):
        if phase+'_dollars' not in c.get(field,{}):c.setdefault(field,{})[phase+'_dollars']=c[field].get(phase)
      c['volume_fp']=c.get('volume_fp',c.get('volume',0))
    else:
     raw=get('/markets/candlesticks',{'market_tickers':m['ticker'],'start_ts':t-660,'end_ts':t+120,'period_interval':1});cs=next((r['candlesticks'] for r in raw.get('markets',[]) if r['market_ticker']==m['ticker']),[])
   out[m['ticker']]=cs
  save(dest,out);count+=1
  if count%100==0:print('EARLY',series,count,'/',len(events),flush=True)
 save(d/'receipt.json',{'events':len(events),'markets':len(ms),'missing':missing});print('EARLY_DONE',series,len(events),len(missing),flush=True)

def analyze():
 pools=defaultdict(list);coverage={}
 for series in ('KXMLBGAME','KXWNBAGAME'):
  mult=read(P/'sports'/series/'series.json')['fee_multiplier'];n=missing=0;seen_events=set()
  for root in (D,P/'sports'):
   d=root/series;events=defaultdict(list)
   for m in read(d/'markets.json'):events[m['event_ticker']].append(m)
   for et,items in events.items():
    mp=d/(et+'_milestone.json');cp=d/(et+'_candles.json.gz')
    if not mp.exists() or not cp.exists():missing+=1;continue
    mile=read(mp);start=ts(mile['start_date']);t=((start-1800)//60)*60
    if et in seen_events:continue
    seen_events.add(et)
    if not START_OLD<=t<END:continue
    cm=read(cp);n+=1
    for prof,low,high in (('pregame70',.70,.90),('pregame55',.55,.80)):
     choices=[]
     for m in items:
      cs={c['end_period_ts']:c for c in cm.get(m['ticker'],[])}
      for side in ('yes','no'):
       q=quote(cs.get(t),side)
       if q is None:continue
       b,a=q
       if not low<=a<high or (a+b)/2<=.5 or a-b>.02000001:continue
       if sum(float(c.get('volume_fp') or 0) for tt,c in cs.items() if t-600<tt<=t)<5:continue
       cl=ts(m['close_time']);ge=ts(mile['end_date']) if mile.get('end_date') else cl
       choices.append(dict(kind='sports',asset=series,event=et,ticker=m['ticker'],side=side,profile=prof,t=t,ask=a,bid=b,cap=min(a+.01,.99),close=cl,game_end=ge,_cs=cs,_market=m,_mult=mult))
     if choices:
      s=min(choices,key=lambda x:(x['ask'],x['ticker'],x['side']))
      for label,stress,mode in SCENARIOS:
       r=execute(s,mode)
       if r:pools[(prof,label)].append(r)
  coverage[series]={'evaluated':n,'missing_files':missing}
 results={};ledgers={}
 for (prof,label),rows in pools.items():
  stress=.02 if label=='cost2c' else .01
  for asset in ('ALL','KXMLBGAME','KXWNBAGAME'):
   rr=rows if asset=='ALL' else [r for r in rows if r['asset']==asset]
   key=asset+'/'+prof+'/'+label;m=record(results,ledgers,key,rr,stress,False,SPLIT_OLD)
   print('EXTENDED',key,{k:v for k,v in m.items() if k in ('trades','wins','losses','scalar','net','drawdown','positive_weeks','active_weeks')},flush=True)
 save(P/'extended_sports_results.json',results);save(P/'extended_sports_ledgers.json.gz',ledgers);save(P/'extended_sports_coverage.json',coverage)
if __name__=='__main__':
 import sys
 if 'analyze' in sys.argv:analyze()
 else:
  with ThreadPoolExecutor(max_workers=2) as ex:list(ex.map(lambda a:pull(*a),[('KXMLBGAME','baseball_game'),('KXWNBAGAME','basketball_game')]))
  analyze()
