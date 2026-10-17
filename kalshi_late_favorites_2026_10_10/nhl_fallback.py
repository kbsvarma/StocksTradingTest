from common import *
from concurrent.futures import ThreadPoolExecutor,as_completed
from zoneinfo import ZoneInfo
from collections import Counter
ET=ZoneInfo('America/New_York');EX=P/'external';EX.mkdir(exist_ok=True)
ALIAS={'NJ':'NJD','LA':'LAK','SJ':'SJS','TB':'TBL','MON':'MTL','UTAH':'UTA','WSH':'WSH','WAS':'WSH'}
def ext(url,name):
 dest=EX/(name+'.json.gz')
 if dest.exists():return read(dest)['response']
 for n in range(3):
  try:r=requests.get(url,timeout=25);r.raise_for_status();x=r.json();save(dest,{'url':url,'retrieved_ts':time.time(),'response':x});return x
  except requests.RequestException:
   if n==2:raise
   time.sleep(n+1)
def canon(a):return ALIAS.get(a,a)
def main():
 d=P/'data/KXNHLGAME';ms=read(d/'markets.json');by={m['ticker']:m for m in ms};jobs=[];receipt=[]
 for f in sorted(d.glob('*_milestone.json')):
  m=read(f);et=m['details']['main_game_event_ticker'];start=ts(m['start_date']);date=datetime.fromtimestamp(start,ET).strftime('%Y%m%d');games=ext('https://site.api.espn.com/apis/site/v2/sports/hockey/nhl/scoreboard?dates='+date+'&limit=100','nhl_schedule_'+date).get('events',[])
  teams={next(iter(x['custom_strike'].values())):canon(x['ticker'].rsplit('-',1)[1]) for x in ms if x['event_ticker']==et};home=teams.get(m['details']['home_team_id']);away=teams.get(m['details']['away_team_id']);matches=[]
  for g in games:
   c=g['competitions'][0];parts={r['homeAway']:canon(r['team']['abbreviation']) for r in c['competitors']}
   if parts=={'home':home,'away':away} and abs(ts(g['date'])-start)<=6*3600:matches.append(g)
  if len(matches)!=1:receipt.append({'event':et,'status':'ambiguous_or_missing','matches':len(matches),'home':home,'away':away});continue
  jobs.append((et,matches[0]['id']))
 def fetch(j):
  et,eid=j;x=ext('https://site.api.espn.com/apis/site/v2/sports/hockey/nhl/summary?event='+eid,'nhl_summary_'+eid);save(d/(et+'_espn.json.gz'),x);return {'event':et,'espn_id':eid,'status':'saved','plays':len(x.get('plays') or []),'wallclock_available':x.get('wallclockAvailable')}
 with ThreadPoolExecutor(max_workers=3) as ex:
  for i,f in enumerate(as_completed([ex.submit(fetch,j) for j in jobs]),1):
   try:receipt.append(f.result())
   except Exception as e:receipt.append({'status':'error','error':str(e)})
   if i%25==0:print('NHL',i,'/',len(jobs),flush=True)
 save(P/'nhl_fallback_receipt.json',receipt);print(Counter(r['status'] for r in receipt),flush=True)
if __name__=='__main__':main()
