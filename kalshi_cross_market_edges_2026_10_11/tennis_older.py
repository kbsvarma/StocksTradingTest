"""Extend a fixed exploratory lead into an additional earlier fortnight."""
from concurrent.futures import ThreadPoolExecutor
import collections
import tennis_markov as tm
from tennis_markov import P,public,read,ts,day,save
ROOT=P/'tennis_older_inputs'
def collect():
 cutoff=ts(public.get('/historical/cutoff')['market_settled_ts'])
 for asset in ('KXWTAMATCH','KXATPMATCH'):
  dest=ROOT/asset;dest.mkdir(parents=True,exist_ok=True)
  mf=dest/'markets.json'
  if mf.exists() and (dest/'combined_catalogue.json').exists():ms=read(mf)
  else:
   allms=public.paged('/historical/markets',{'series_ticker':asset,'limit':1000},'markets')+public.paged('/markets',{'series_ticker':asset,'status':'settled','limit':1000,'min_close_ts':ts('2026-09-11T04:00:00Z'),'max_close_ts':ts('2026-09-27T04:00:00Z')},'markets')
   ms=list({m['ticker']:m for m in allms if ts('2026-09-11T04:00:00Z')<=ts(m['close_time'])<ts('2026-09-27T04:00:00Z')}.values());public.save(mf,ms);public.save(dest/'combined_catalogue.json',dict(markets=len(ms),cutoff=cutoff))
  public.save(dest/'series.json',read(tm.ROOT/asset/'series.json'));groups=collections.defaultdict(list)
  for m in ms:groups[m['event_ticker']].append(m)
  print('OLDER_MARKETS',asset,len(ms),'events',len(groups),flush=True)
  def one(job):
   et,items=job;cp=dest/(et+'_candles.json.gz');mp=dest/(et+'_milestones.json');pp=dest/(et+'_plays.json.gz')
   try:
    if not mp.exists():public.save(mp,public.get('/milestones',{'limit':100,'related_event_ticker':et})['milestones'])
    miles=[r for r in read(mp) if r.get('details',{}).get('main_game_event_ticker')==et]
    if len(miles)!=1 or str(miles[0]['details'].get('best_of'))!='3':return
    start=ts(miles[0]['start_date'])
    if not '2026-09-12'<=day(start)<='2026-09-25':return
    if not pp.exists():public.save(pp,public.get('/live_data/milestone/'+miles[0]['id']+'/game_stats'))
    if not read(pp).get('pbp',{}).get('periods'):return
    if not cp.exists():
     cm={m['ticker']:public.get(('/historical/markets/'+m['ticker'] if ts(m['settlement_ts'])<cutoff else '/series/'+asset+'/markets/'+m['ticker'])+'/candlesticks',{'start_ts':start-3600,'end_ts':ts(m['close_time']),'period_interval':1}).get('candlesticks',[]) for m in items};public.save(cp,cm)
   except Exception as ex:public.save(dest/(et+'_error.json'),{'error':str(ex)})
  with ThreadPoolExecutor(max_workers=4) as ex:
   for i,_ in enumerate(ex.map(one,sorted(groups.items())),1):
    if i%30==0:print('OLDER_FETCH',asset,i,len(groups),flush=True)
if __name__=='__main__':
 collect();tm.ROOT=ROOT;tm.run(100,'2026-09-12','2026-09-25',[('older','2026-09-12','2026-09-25')],only=(.5,.06),output_prefix='tennis_older_')
