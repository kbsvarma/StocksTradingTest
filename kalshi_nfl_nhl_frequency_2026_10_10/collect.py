"""Bounded NFL/NHL public-history collection with exact team/date matching."""
from common import *
from collections import defaultdict,Counter
from concurrent.futures import ThreadPoolExecutor,as_completed
from zoneinfo import ZoneInfo
import re,shutil
from datetime import timedelta

ET=ZoneInfo('America/New_York')
ALIAS={'NJ':'NJD','LA':'LAK','SJ':'SJS','TB':'TBL','MON':'MTL','UTAH':'UTA','WAS':'WSH','JAC':'JAX','OAK':'LV','SD':'LAC'}
def canon(a,s):
 if s=='KXNFLGAME':return {'LA':'LAR','WSH':'WAS','JAC':'JAX','OAK':'LV','SD':'LAC'}.get(a,a)
 return ALIAS.get(a,a)
def ext(url,name):
 dest=P/'external'/(name+'.json.gz')
 if dest.exists():return read(dest)['response']
 for n in range(4):
  try:
   resp=requests.get(url,timeout=25);resp.raise_for_status();x=resp.json();save(dest,{'url':url,'retrieved_ts':time.time(),'response':x});return x
  except requests.RequestException:
   if n==3:raise
   time.sleep(n+1)
def datestr(et):
 q=re.search(r'-(\d{2}[A-Z]{3}\d{2})',et).group(1)
 return datetime.strptime(q,'%y%b%d').strftime('%Y%m%d')
def league(s):return ('football','nfl') if s=='KXNFLGAME' else ('hockey','nhl')

def main():
 jobs=[];receipt=[];grouped={};schedules={}
 for s in ['KXNFLGAME','KXNHLGAME']:
  hist=read(P/(s+'_previous_markets.json'))
  current=paged('/markets',{'series_ticker':s,'status':'settled','limit':1000},'markets')
  save(P/(s+'_current_markets.json'),current)
  ms={m['ticker']:m for m in hist+current};groups=defaultdict(list)
  for m in ms.values():
   if m.get('result') not in ('yes','no') and m.get('settlement_value_dollars') is None:continue
   dt=m['close_time'][:10]
   prior=('2025-09-01'<=dt<'2026-02-16') if s=='KXNFLGAME' else ('2026-01-01'<=dt<'2026-04-01')
   recent='2026-08-01'<=dt<'2026-10-11'
   if prior or recent:groups[m['event_ticker']].append(m)
  grouped[s]=groups;sport,l=league(s)
  for date in sorted({datestr(et) for et in groups}):
   schedules[(s,date)]=ext(f'https://site.api.espn.com/apis/site/v2/sports/{sport}/{l}/scoreboard?dates={date}&limit=100',l+'_schedule_'+date)
  print('CATALOG',s,len(groups),flush=True)
  for et,items in sorted(groups.items()):
   if len(items)!=2:receipt.append({'series':s,'event':et,'status':'not_two_markets'});continue
   date=datestr(et);teams={canon(m['ticker'].rsplit('-',1)[1],s):next(iter(m.get('custom_strike',{}).values()),None) for m in items};matches=[]
   for g in schedules[(s,date)].get('events',[]):
    parts={r['homeAway']:canon(r['team']['abbreviation'],s) for r in g['competitions'][0]['competitors']}
    if set(parts.values())==set(teams):matches.append((g,parts))
   if not matches:
    # NFL flexed games can retain the originally scheduled date in their
    # ticker. Match exact teams on adjacent dates; ambiguity is excluded.
    for delta in [-1,1]:
     alt=(datetime.strptime(date,'%Y%m%d')+timedelta(days=delta)).strftime('%Y%m%d')
     if (s,alt) not in schedules:schedules[(s,alt)]=ext(f'https://site.api.espn.com/apis/site/v2/sports/{sport}/{l}/scoreboard?dates={alt}&limit=100',l+'_schedule_'+alt)
     for g in schedules[(s,alt)].get('events',[]):
      parts={r['homeAway']:canon(r['team']['abbreviation'],s) for r in g['competitions'][0]['competitors']}
      if set(parts.values())==set(teams):matches.append((g,parts))
   if len(matches)!=1:receipt.append({'series':s,'event':et,'status':'ambiguous_or_missing_schedule','matches':len(matches),'teams':list(teams)});continue
   g,parts=matches[0];jobs.append((s,et,items,g,parts,teams))
 save(P/'collection_jobs.json',[(s,et,g['id']) for s,et,_,g,_,_ in jobs]);print('JOBS',len(jobs),flush=True)
 def one(job):
  s,et,items,g,parts,teams=job;d=P/'data'/s;d.mkdir(parents=True,exist_ok=True);sport,l=league(s)
  ep=d/(et+'_espn.json.gz');cp=d/(et+'_candles.json.gz');mp=d/(et+'_milestone.json')
  y=read(ep) if ep.exists() else ext(f'https://site.api.espn.com/apis/site/v2/sports/{sport}/{l}/summary?event='+g['id'],l+'_summary_'+g['id'])
  if not ep.exists():save(ep,y)
  head=y.get('header',{});comp=(head.get('competitions') or [{}])[0]
  hp={a['homeAway']:canon(a['team']['abbreviation'],s) for a in comp.get('competitors',[])}
  if hp!=parts:raise ValueError('summary team mismatch '+et)
  season=(head.get('season') or g.get('season') or {}).get('type');season={1:'PRE',2:'REG',3:'POST'}.get(season,'unknown')
  espnteams={a['team']['id']:teams[canon(a['team']['abbreviation'],s)] for a in comp['competitors']}
  start=ts(g['date']);end=max(ts(m['close_time']) for m in items)
  mile={'id':'espn:'+g['id'],'start_date':g['date'],'end_date':datetime.fromtimestamp(end,ZoneInfo('UTC')).isoformat(),'details':{'main_game_event_ticker':et,'home_team_id':teams[parts['home']],'away_team_id':teams[parts['away']],'season_type':season,'espn_team_mapping':espnteams},'provenance':'Exact ESPN home/away/date to Kalshi target mapping; game end capped by market close and last timed play, never used to define late-game eligibility.'}
  save(mp,mile)
  if not cp.exists():
   old=P.parent/'kalshi_noncrypto_consistency_2026_10_10/sports'/s/cp.name
   if old.exists():shutil.copy2(old,cp)
   else:
    out={}
    for m in items:
     params={'start_ts':start-3600,'end_ts':min(max(end+120,start+8*3600),start+12*3600),'period_interval':1};ticker=m['ticker']
     if m['close_time']<'2026-08-11':z=get('/historical/markets/'+ticker+'/candlesticks',params);cs=z.get('candlesticks',[])
     else:z=get('/markets/candlesticks',{**params,'market_tickers':ticker});cs=next((v['candlesticks'] for v in z.get('markets',[]) if v['market_ticker']==ticker),[])
     out[ticker]=cs
    save(cp,out)
  corrected=[]
  for m in items:
   m=dict(m)
   if ts(m['close_time'])<start:
    proof=get('/historical/trades',{'ticker':m['ticker'],'min_ts':start,'max_ts':min(ts(m['settlement_ts']),start+12*3600),'limit':3})
    trades=[t for t in proof.get('trades',[]) if t.get('ticker')==m['ticker'] and ts(t['created_time'])>=start]
    if not trades:raise ValueError('close before kickoff; no official in-game trade proof '+et)
    verified=max(ts(t['created_time']) for t in trades)+1
    m['_original_close_time']=m['close_time'];m['_close_correction']='Stale pregame close field contradicted by official executed trades. Conservatively cap at latest verified in-game trade +1 second.';m['close_time']=datetime.fromtimestamp(verified,ZoneInfo('UTC')).isoformat()
    save(P/'close_corrections'/(m['ticker']+'.json'),proof)
   corrected.append(m)
  if any('_original_close_time' in m for m in corrected):
   mile['end_date']=datetime.fromtimestamp(max(ts(m['close_time']) for m in corrected),ZoneInfo('UTC')).isoformat();save(mp,mile)
  items=corrected
  return {'series':s,'event':et,'status':'saved','season_type':season,'espn_id':g['id'],'markets':items}
 with ThreadPoolExecutor(max_workers=3) as pool:
  for n,f in enumerate(as_completed([pool.submit(one,j) for j in jobs]),1):
   try:receipt.append(f.result())
   except Exception as ex:receipt.append({'status':'error','error':str(ex)})
   if n%50==0:print('FETCH',n,'/',len(jobs),dict(Counter(r['status'] for r in receipt)),flush=True)
 for s in grouped:
  save(P/'data'/s/'markets.json',[m for r in receipt if r.get('series')==s and r['status']=='saved' for m in r['markets']]);save(P/'data'/s/'series.json',get('/series/'+s)['series'])
 for r in receipt:r.pop('markets',None)
 save(P/'collection_receipt.json',receipt);print('DONE',dict(Counter(r['status'] for r in receipt)),flush=True)
if __name__=='__main__':main()
