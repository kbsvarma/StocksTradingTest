"""Public GET only. Separate archive; no production dependencies."""
import sys, json, gzip, time, shutil
from pathlib import Path
from collections import defaultdict,Counter
from concurrent.futures import ThreadPoolExecutor,as_completed
from zoneinfo import ZoneInfo
from common import *
ROOT=P.parent
SPORTS={'KXMLBGAME':'Pro Baseball','KXNFLGAME':'Pro Football','KXNCAAFGAME':'College Football','KXNBAGAME':'Pro Basketball (M)','KXWNBAGAME':'Pro Basketball (W)','KXNHLGAME':'Pro Hockey'}
SPORT_START=ts('2026-08-15T04:00:00Z')
def finance_catalog():
 x=get('/series',{'include_product_metadata':'true'},cache=False);save(P/'series.json',x)
 selected=[s for s in x['series'] if s.get('frequency')=='hourly' and s.get('category') in ('Financials','Commodities')]
 save(P/'financial_universe.json',selected)
 for s in selected:
  d=P/'financial'/s['ticker'];d.mkdir(parents=True,exist_ok=True);save(d/'series.json',s)
  ms=paged('/markets',{'series_ticker':s['ticker'],'min_close_ts':START,'max_close_ts':END,'limit':1000},'markets')
  ms=[m for m in ms if START<=ts(m['close_time'])<END];save(d/'markets.json',ms)
  print('FINANCIAL',s['ticker'],len(ms),len({m['event_ticker'] for m in ms}),flush=True)
def sports_catalog():
 for s,comp in SPORTS.items():
  d=P/'sports'/s;d.mkdir(parents=True,exist_ok=True);save(d/'series.json',get('/series/'+s)['series'])
  ms=paged('/markets',{'series_ticker':s,'min_close_ts':SPORT_START-3*86400,'max_close_ts':END+3*86400,'limit':1000},'markets')
  ms=[m for m in ms if SPORT_START-3*86400<=ts(m['close_time'])<END+3*86400];save(d/'markets.json',ms)
  miles=paged('/milestones',{'competition':comp,'minimum_start_date':'2026-08-14T00:00:00Z','limit':500},'milestones')
  save(d/'milestones.json',miles)
  print('SPORT',s,len(ms),len(miles),flush=True)
def pull_financial(s):
 d=P/'financial'/s;ms=read(d/'markets.json');events=defaultdict(list)
 for m in ms:
  if s in ('KXGOLDH','KXSILVERH','KXWTIH','KXPALLADIUMH') and datetime.fromtimestamp(ts(m['close_time']),ZoneInfo('America/New_York')).hour!=16:continue
  if m.get('strike_type') in ('greater','greater_or_equal') and m.get('floor_strike') is not None and float(m.get('volume_fp') or 0)>=5:events[m['event_ticker']].append(m)
 errors=[]
 for i,(et,items) in enumerate(sorted(events.items())):
  cp=d/(et+'_chart.json.gz');qp=d/(et+'_candles.json.gz')
  try:
   if not cp.exists():
    old=ROOT/'kalshi_market_expansion_2026_10_09/indices/data'/s/(et+'_chart.json.gz')
    if old.exists():save(cp,read(old)['response'])
    else:save(cp,get('/live_data/events/'+et,{'range':'1h'}))
   chart=read(cp)
   if not chart.get('live_data',{}).get('details',{}).get('timeseries'):
    errors.append({'event':et,'error':'no_source_chart'});continue
   if not qp.exists():
    cl=max(ts(m['close_time']) for m in items);out={}
    for j in range(0,len(items),100):
     sub=items[j:j+100];x=get('/markets/candlesticks',{'market_tickers':','.join(m['ticker'] for m in sub),'start_ts':cl-95*60,'end_ts':cl,'period_interval':1})
     out.update({r['market_ticker']:r['candlesticks'] for r in x.get('markets',[])})
    save(qp,out)
  except Exception as ex:errors.append({'event':et,'error':str(ex)});save(d/(et+'_error.json'),{'error':str(ex)})
  if i%50==0:print('FIN',s,i+1,'/',len(events),'errors',len(errors),flush=True)
 save(d/'receipt.json',{'events':len(events),'markets':len(ms),'errors':errors,'finished':time.time()});print('FIN_DONE',s,len(events),'errors',len(errors),flush=True)
def pull_sports(s):
 d=P/'sports'/s;ms=read(d/'markets.json');events=defaultdict(list)
 for m in ms:events[m['event_ticker']].append(m)
 miles=read(P/'milestones_all.json') if (P/'milestones_all.json').exists() else read(d/'milestones.json');byevent=defaultdict(list)
 for x in miles:
  et=x.get('details',{}).get('main_game_event_ticker')
  if et:byevent[et].append(x)
 jobs=[];missing=[]
 for et,items in sorted(events.items()):
  mile=byevent.get(et,[])
  old=ROOT/'kalshi_broad_review_2026_10_10/replays'/s
  if len(mile)!=1 and (old/(et+'_milestones.json')).exists():mile=[x for x in read(old/(et+'_milestones.json')) if x.get('details',{}).get('main_game_event_ticker')==et]
  if len(mile)!=1:missing.append({'event':et,'reason':'missing_or_ambiguous_start'});continue
  mile=mile[0];save(d/(et+'_milestone.json'),mile)
  cl=max(ts(m['close_time']) for m in items);start=ts(mile['start_date'])
  if not SPORT_START<=start<END:missing.append({'event':et,'reason':'start_outside_period','start':start,'close':cl});continue
  end=ts(mile['end_date']) if mile.get('end_date') else min(start+6*3600,cl)
  lo=max(min(ts(m['open_time']) for m in items),start-65*60);hi=min(cl,end+120,start+6*3600)
  if hi<=lo:missing.append({'event':et,'reason':'empty_interval'});continue
  if (d/(et+'_candles.json.gz')).exists():continue
  jobs.append((et,items,lo,hi))
 # Batch games with nearby time ranges, never exceed 9000 theoretical candles.
 groups=[];group=[];n=0;lo=hi=0
 for job in sorted(jobs,key=lambda x:x[2]):
  jt,jms,jlo,jhi=job;newlo=min(lo,jlo) if group else jlo;newhi=max(hi,jhi) if group else jhi
  if group and ((n+len(jms))*((newhi-newlo)//60+2)>9000 or n+len(jms)>100):groups.append((group,lo,hi));group=[];n=0;newlo=jlo;newhi=jhi
  group.append(job);n+=len(jms);lo=newlo;hi=newhi
 if group:groups.append((group,lo,hi))
 for i,(jobs,lo,hi) in enumerate(groups):
  ticks=[m['ticker'] for j in jobs for m in j[1]]
  try:
   x=get('/markets/candlesticks',{'market_tickers':','.join(ticks),'start_ts':lo,'end_ts':hi,'period_interval':1});cs={r['market_ticker']:r['candlesticks'] for r in x.get('markets',[])}
   for et,items,jlo,jhi in jobs:save(d/(et+'_candles.json.gz'),{m['ticker']:[c for c in cs.get(m['ticker'],[]) if jlo<=c['end_period_ts']<=jhi] for m in items})
  except Exception as ex:
   for j in jobs:missing.append({'event':j[0],'reason':'candle_request_error','error':str(ex)})
  if i%25==0:print('SPORT_CANDLES',s,i+1,'/',len(groups),flush=True)
 save(d/'receipt.json',{'events':len(events),'markets':len(ms),'errors':missing,'finished':time.time()});print('SPORT_DONE',s,len(events),'missing',len(missing),flush=True)
if __name__=='__main__':
 mode=sys.argv[1]
 if mode=='catalog':finance_catalog();sports_catalog()
 elif mode=='finance':
  names=sys.argv[2:] or [p.name for p in (P/'financial').iterdir() if read(p/'markets.json')]
  with ThreadPoolExecutor(max_workers=2) as ex:list(ex.map(pull_financial,names))
 elif mode=='sports':
  with ThreadPoolExecutor(max_workers=2) as ex:list(ex.map(pull_sports,sys.argv[2:] or SPORTS))
