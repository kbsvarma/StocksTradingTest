from common import *
from collections import defaultdict,Counter
from concurrent.futures import ThreadPoolExecutor,as_completed
import shutil

SERIES=['KXMLBGAME','KXNFLGAME','KXNCAAFGAME','KXNBAGAME','KXWNBAGAME','KXNHLGAME']
BEGIN=ts('2026-08-15T04:00:00Z');FINISH=ts('2026-10-10T04:00:00Z')
SOURCE=P.parent/'kalshi_noncrypto_consistency_2026_10_10'/'sports'

def prepare():
 jobs=[];coverage={}
 for s in SERIES:
  src=SOURCE/s;d=P/'data'/s;d.mkdir(parents=True,exist_ok=True);ms=read(src/'markets.json');groups=defaultdict(list);c=Counter()
  for m in ms:groups[m['event_ticker']].append(m)
  shutil.copy2(src/'series.json',d/'series.json');kept=[]
  for et,items in sorted(groups.items()):
   c['catalogued_events']+=1;mp=src/(et+'_milestone.json');cp=src/(et+'_candles.json.gz')
   if not mp.exists() or not cp.exists():c['missing_milestone_or_quotes']+=1;continue
   mile=read(mp);start=ts(mile['start_date'])
   if not BEGIN<=start<FINISH:c['outside_dates']+=1;continue
   if not mile.get('end_date'):c['missing_end']+=1;continue
   # Public quote-only prefilter, independent of who eventually won. Fetch all
   # PBP for games with a potential 90-98c choice somewhere before game end.
   end=ts(mile['end_date']);cm=read(cp);candidate=False
   for cs in cm.values():
    for row in cs:
     if not start<=row['end_period_ts']<end:continue
     try:b=float(row['yes_bid']['close_dollars']);a=float(row['yes_ask']['close_dollars'])
     except (KeyError,TypeError,ValueError):continue
     if not 0<=b<=a<=1 or a-b>.02000001:continue
     if .9<=a<.98 or .9<=1-b<.98:candidate=True;break
    if candidate:break
   if not candidate:c['no_candidate_price_before_end']+=1;continue
   c['pbp_jobs']+=1;kept+=items
   for f in [mp,cp]:
    if not (d/f.name).exists():shutil.copy2(f,d/f.name)
   jobs.append((s,et,mile['id']))
  save(d/'markets.json',kept);coverage[s]=dict(c)
 save(P/'collection_coverage.json',coverage);save(P/'collection_jobs.json',jobs);print('PREPARED',len(jobs),coverage,flush=True)
 return jobs

def one(job):
 s,et,mid=job;d=P/'data'/s;out=d/(et+'_plays.json.gz')
 if out.exists():return s,'cached'
 try:
  x=get('/live_data/milestone/'+mid+'/game_stats');save(out,x);return s,'saved'
 except Exception as e:save(d/(et+'_plays_error.json'),{'error':str(e)});return s,'error'

if __name__=='__main__':
 jobs=prepare();counts=Counter()
 with ThreadPoolExecutor(max_workers=3) as ex:
  for n,f in enumerate(as_completed([ex.submit(one,j) for j in jobs]),1):
   s,result=f.result();counts[s+'/'+result]+=1
   if n%50==0 or n==len(jobs):print('FETCH',n,'/',len(jobs),dict(counts),flush=True)
 save(P/'collection_receipt.json',{'finished_ts':time.time(),'jobs':len(jobs),'results':dict(counts)})
