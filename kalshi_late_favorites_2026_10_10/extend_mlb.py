from common import *
from collections import defaultdict,Counter
from concurrent.futures import ThreadPoolExecutor,as_completed
import shutil
BEGIN=ts('2026-07-01T04:00:00Z');CUT=ts('2026-08-15T04:00:00Z')
OLD=P.parent/'kalshi_noncrypto_consistency_2026_10_10/sports_earlier/KXMLBGAME'
ARCHIVE=P.parent/'research_data/StocksTradingTest/kalshi_sports_2026_08/data/games_KXMLBGAME.jsonl'
D=P/'earlier/KXMLBGAME';D.mkdir(parents=True,exist_ok=True)

def normalize(cs):
 for c in cs:
  for field in ['yes_bid','yes_ask']:
   for phase in ['open','close']:
    if phase+'_dollars' not in c.get(field,{}):
     v=c.get(field,{}).get(phase)
     if v is not None:c.setdefault(field,{})[phase+'_dollars']=str(float(v)/100 if isinstance(v,int) else v)
  c['volume_fp']=c.get('volume_fp',c.get('volume',0))
 return cs

def main():
 ms=read(OLD/'markets.json');groups=defaultdict(list);valid={};jobs=[];ct=Counter()
 for m in ms:groups[m['event_ticker']].append(m)
 for et,items in groups.items():
  mp=OLD/(et+'_milestone.json')
  if not mp.exists():ct['missing_milestone']+=1;continue
  mile=read(mp)
  if not BEGIN<=ts(mile['start_date'])<CUT:ct['outside']+=1;continue
  if not mile.get('end_date'):ct['missing_end']+=1;continue
  valid[et]=(items,mile);shutil.copy2(mp,D/mp.name)
 save(D/'markets.json',[m for items,_ in valid.values() for m in items]);shutil.copy2(P/'data/KXMLBGAME/series.json',D/'series.json')
 needed={m['ticker'] for items,_ in valid.values() for m in items};arch={}
 with ARCHIVE.open() as f:
  for line in f:
   r=json.loads(line)
   if r['ticker'] in needed:arch[r['ticker']]=normalize(r.get('candles',[]))
 def one(job):
  et,(items,mile)=job;cp=D/(et+'_candles.json.gz');pp=D/(et+'_plays.json.gz');start=ts(mile['start_date']);end=min(ts(mile['end_date']),max(ts(m['close_time']) for m in items));out={}
  if not cp.exists():
   prefile=OLD/(et+'_candles.json.gz');pre=read(prefile) if prefile.exists() else {}
   for m in items:
    t=m['ticker'];cs={c['end_period_ts']:c for c in normalize(pre.get(t,[]))+arch.get(t,[]) if start-3600<=c['end_period_ts']<=end+120}
    # Existing original archive must reach into the game; pregame-only data
    # does not count as an in-game replay. Fetch full interval when absent.
    if not any(start+3600<=x<=end for x in cs):
     params={'start_ts':start-3600,'end_ts':end+120,'period_interval':1}
     if ts(m['settlement_ts'])<ts('2026-08-11T00:00:00Z'):
      z=get('/historical/markets/'+t+'/candlesticks',params);new=z.get('candlesticks',[])
     else:
      z=get('/markets/candlesticks',{**params,'market_tickers':t});new=next((a['candlesticks'] for a in z.get('markets',[]) if a['market_ticker']==t),[])
     cs.update({c['end_period_ts']:c for c in normalize(new)})
    out[t]=[cs[x] for x in sorted(cs)]
   save(cp,out)
  if not pp.exists():save(pp,get('/live_data/milestone/'+mile['id']+'/game_stats'))
  return et
 errs=[]
 with ThreadPoolExecutor(max_workers=3) as ex:
  for n,f in enumerate(as_completed([ex.submit(one,j) for j in valid.items()]),1):
   try:f.result()
   except Exception as e:errs.append(str(e))
   if n%50==0 or n==len(valid):print('EARLY_MLB',n,'/',len(valid),'errors',len(errs),flush=True)
 save(P/'earlier_collection_receipt.json',{'events':len(valid),'excluded':dict(ct),'errors':errs,'finished_ts':time.time()})
if __name__=='__main__':main()
