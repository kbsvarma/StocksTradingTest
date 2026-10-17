from common import *
from portfolio import quote,fee,payout,record
from collections import Counter,defaultdict
from zoneinfo import ZoneInfo
import math,bisect,itertools

SERIES=['KXMLBGAME','KXNFLGAME','KXNCAAFGAME','KXNBAGAME','KXWNBAGAME','KXNHLGAME']
START=ts('2026-08-15T04:00:00Z');END=ts('2026-10-10T04:00:00Z');SPLIT=ts('2026-09-12T04:00:00Z')
PROFILES=[f'{family}/{clock}/{band}' for family,clock,band in itertools.product(['leader','comfortable','original_favorite'],['late','very_late'],['p90','p95'])]
SCENARIOS=[('base',30,'open',.01),('cost2c',30,'open',.02),('delay60',30,'close',.01),('source90',90,'open',.01),('both_delays',90,'close',.01)]
THRESHOLD={'KXMLBGAME':3,'KXNFLGAME':9,'KXNCAAFGAME':9,'KXNBAGAME':8,'KXWNBAGAME':8,'KXNHLGAME':2}
FEE_CHANGES=read(P/'series_fee_changes.json')['series_fee_change_arr'] if (P/'series_fee_changes.json').exists() else []
def fee_multiplier_at(s,t,default):
 known=[r for r in FEE_CHANGES if r['series_ticker']==s and ts(r['scheduled_ts'])<=t]
 return max(known,key=lambda r:ts(r['scheduled_ts']))['fee_multiplier'] if known else default

def clock_seconds(x):
 try:
  a,b=x.split(':');return 60*int(a)+float(b)
 except (TypeError,ValueError,AttributeError):return None

def parse_states(x,mile,s,espn=None):
 start=ts(mile['start_date']);end=ts(mile['end_date']);out=[];coverage=Counter();raw=[]
 for p in (x.get('pbp') or {}).get('periods') or []:
  for e in p.get('events') or []:
   for ee in e.get('plays') or [e]:raw.append((p,ee))
 coverage['raw_events']=len(raw)
 use_espn=s=='KXNHLGAME' and not any(e.get('wall_clock') for _,e in raw) and espn is not None
 if use_espn:
  coverage['espn_fallback']=1;raw=[]
  for e in espn.get('plays') or []:
   elapsed=clock_seconds((e.get('clock') or {}).get('displayValue'));period=(e.get('period') or {}).get('number');stamp=e.get('wallclock')
   try:w=ts(stamp) if stamp else None
   except (ValueError,TypeError):w=None
   raw.append(({'period_number':period},{'home_points':e.get('homeScore'),'away_points':e.get('awayScore'),'wall_clock':w,'clock_seconds':1200-elapsed if elapsed is not None else None,'description':e.get('text'),'sequence':e.get('sequenceNumber'),'wall_clock_source':'ESPN_event_time','modified':e.get('modified')}))
  coverage['raw_events']=len(raw)
 for n,(p,e) in enumerate(raw):
  if not e.get('wall_clock'):coverage['no_wall_clock']+=1;continue
  try:w=int(e['wall_clock']);per=int(p['period_number']);h=int(e['home_points']);a=int(e['away_points'])
  except (ValueError,TypeError,KeyError):coverage['malformed_state']+=1;continue
  if not start-600<=w<=end+600:coverage['outside_game_timestamps']+=1;continue
  clock=e.get('clock_seconds') if 'clock_seconds' in e else clock_seconds(e.get('clock'));half=e.get('half') or p.get('half');outs=e.get('outs');seq=e.get('sequence')
  try:order=float(seq) if seq is not None else -n
  except (ValueError,TypeError):order=-n
  if s=='KXMLBGAME':
   terminal=per>=9 and ((half=='top' and outs==3 and h>a) or (half=='bottom' and (h>a or (outs==3 and h!=a))))
  else:terminal=(per>=(3 if s=='KXNHLGAME' else 4) and clock==0 and h!=a)
  out.append({'wall':w,'order':order,'period':per,'clock':clock,'home':h,'away':a,'half':half,'outs':outs,'terminal':terminal,'source':e.get('wall_clock_source','provider_event_time'),'description':e.get('description',''),'modified':e.get('modified')})
 # A late-arriving old play may not reverse the current score state. Sequence
 # is available at the time of the play; no future play data is consulted.
 out.sort(key=lambda r:(r['wall'],r['order']));states=[];best=-math.inf
 for r in out:
  if r['order']<best:coverage['out_of_order_older_play']+=1;continue
  best=r['order'];states.append(r)
 if states:
  # Last archived play/end metadata is used only to reject after-game fills,
  # never to decide when the "last five minutes" begins.
  end=min(end,max(r['wall'] for r in states))
 coverage['usable_states']=len(states)
 return states,end,dict(coverage)

def at(states,stamps,t,lag):
 j=bisect.bisect_right(stamps,t-lag)-1
 return states[j] if j>=0 else None

def eligible(r,s,clock,family,t,lag,home,away,prefav):
 if not r or r['terminal'] or t-r['wall']>(180 if s=='KXMLBGAME' else 120):return None
 diff=r['home']-r['away']
 if not diff:return None
 team=home if diff>0 else away
 if s=='KXMLBGAME':ok=(7 if clock=='late' else 8)<=r['period']<=9
 else:
  last=3 if s=='KXNHLGAME' else 4
  limit=(300 if clock=='late' else 120) if s in ('KXNBAGAME','KXWNBAGAME') else(600 if clock=='late' else 300)
  ok=r['period']==last and r['clock'] is not None and 0<r['clock']<=limit
 if not ok:return None
 if family=='comfortable' and abs(diff)<THRESHOLD[s]:return None
 if family=='original_favorite' and team!=prefav:return None
 return team

def prepare_event(ms,cm,mile,s,plays,espn):
 states,end,coverage=parse_states(plays,mile,s,espn);stamps=[r['wall'] for r in states];details=mile['details'];home=details.get('home_team_id');away=details.get('away_team_id');teammap={}
 for m in ms:
  ids=[v for k,v in (m.get('custom_strike') or {}).items() if k.endswith('_team')]
  if len(ids)==1 and ids[0] in (home,away):teammap[m['ticker']]=ids[0]
 csmap={m['ticker']:{c['end_period_ts']:c for c in cm.get(m['ticker'],[])} for m in ms}
 for cs in csmap.values():
  for t,c in cs.items():c['_volume10']=sum(float(cs.get(tt,{}).get('volume_fp') or 0) for tt in range(t-540,t+60,60))
 pre=(ts(mile['start_date'])-1800)//60*60;favorites=set()
 for m in ms:
  if m['ticker'] not in teammap:continue
  q=quote(csmap[m['ticker']].get(pre))
  if q and q[1]-q[0]<=.02000001 and sum(q)/2>=.6:favorites.add(teammap[m['ticker']])
 prefav=next(iter(favorites)) if len(favorites)==1 else None
 coverage['missing_unique_pregame_favorite']=int(prefav is None)
 coverage['mapped_team_markets']=len(teammap)
 # Scalar football tie payouts are complementary (both .50); archived rules
 # must explicitly state this before using NO of the opposing team.
 complementary=len(ms)==2 and set(teammap.values())=={home,away}
 if s in ('KXNFLGAME','KXNCAAFGAME'):
  complementary=complementary and all('tie' in m.get('rules_secondary','').lower() and ('$0.50' in m.get('rules_secondary','') or s=='KXNCAAFGAME') for m in ms)
 return states,stamps,end,coverage,home,away,teammap,csmap,prefav,complementary

def event_rows(ms,cm,mile,s,plays,espn,mult):
 states,stamps,end,cov,home,away,tm,csmap,prefav,complement=prepare_event(ms,cm,mile,s,plays,espn)
 out=defaultdict(list);reject=Counter()
 if not states or not home or not away or len(tm)!=2:return out,cov,dict(reject)
 times=sorted({t for cs in csmap.values() for t in cs if stamps[0]<=t<end});season=mile['details'].get('season_type') or (mile['details'].get('season') or {}).get('type') or 'unknown'
 for label,lag,mode,stress in SCENARIOS:
  for t in times:
   r=at(states,stamps,t,lag)
   if not r:continue
   for clock in ['late','very_late']:
    if not eligible(r,s,clock,'leader',t,lag,home,away,prefav):continue
    for family in ['leader','comfortable','original_favorite']:
     team=eligible(r,s,clock,family,t,lag,home,away,prefav)
     if not team:continue
     for band in ['p90','p95']:
      lo,hi=(.90,.95) if band=='p90' else(.95,.98);choices=[]
      for m in ms:
       if m['ticker'] not in tm:continue
       side='yes' if tm[m['ticker']]==team else 'no'
       if side=='no' and not complement:continue
       cs=csmap[m['ticker']];q=quote(cs.get(t),side)
       if not q:continue
       bid,ask=q
       if not lo<=ask<hi or ask-bid>.02000001 or cs[t]['_volume10']<5:continue
       choices.append((ask,m['ticker'],side,m,cs,bid))
      if not choices:continue
      ask,ticker,side,m,cs,bid=min(choices,key=lambda z:z[:3]);profile=f'{family}/{clock}/{band}';key=profile+'/'+label
      # Choice is fixed before reading any subsequent quote or source update.
      entered=t+(60 if mode=='close' else 3);r2=at(states,stamps,entered,lag)
      if eligible(r2,s,clock,family,entered,lag,home,away,prefav)!=team:reject['execution_state_changed']+=1;continue
      if entered>=min(end,ts(m['close_time'])):reject['after_game_or_market_close']+=1;continue
      q2=quote(cs.get(t+60),side,'close' if mode=='close' else 'open')
      if not q2:reject['missing_execution_quote']+=1;continue
      entry=max(ask,q2[1]);cap=min(.98,ask+.01)
      if entry>cap+1e-8:reject['execution_over_cap']+=1;continue
      pay=payout(m,side)
      if pay is None or not m.get('settlement_ts'):reject['missing_outcome']+=1;continue
      settled=ts(m['settlement_ts'])
      if settled<=entered:reject['settled_before_entry']+=1;continue
      mf=fee_multiplier_at(s,entered,mult)
      out[key].append({'kind':'sports','asset':s,'profile':profile,'event':m['event_ticker'],'ticker':ticker,'side':side,'team':team,'home_team':home,'away_team':away,'pregame_favorite':prefav,'t':t,'entered':entered,'entry':entry,'ask':ask,'bid':bid,'cap':cap,'payout':pay,'won':pay==1 if pay in(0,1) else None,'settled':settled,'close':ts(m['close_time']),'game_end':end,'fee':fee(entry,mf),'fee_multiplier':mf,'source_delay':lag,'score_wall':r2['wall'],'score_age':entered-r2['wall'],'period':r2['period'],'clock':r2['clock'],'home_score':r2['home'],'away_score':r2['away'],'inning_half':r2['half'],'outs':r2['outs'],'score_source':r2['source'],'season_type':season,'source_description':r2['description'],'complement_allowed':complement})
 return out,cov,dict(reject)

def main():
 signals=defaultdict(list);coverage={};gamecov={};rejects=Counter();results={};ledgers={}
 for s in SERIES:
  d=P/'data'/s;ms=read(d/'markets.json');mult=read(d/'series.json')['fee_multiplier'];groups=defaultdict(list);cc=Counter()
  for m in ms:groups[m['event_ticker']].append(m)
  for n,(et,items) in enumerate(sorted(groups.items()),1):
   pp=d/(et+'_plays.json.gz');ep=d/(et+'_espn.json.gz');cc['price_candidate_games']+=1
   if not pp.exists():cc['missing_pbp_file']+=1;continue
   mile=read(d/(et+'_milestone.json'));cm=read(d/(et+'_candles.json.gz'));rows,cov,rej=event_rows(items,cm,mile,s,read(pp),read(ep) if ep.exists() else None,mult)
   gamecov[et]=cov
   for k,v in cov.items():cc[k]+=v
   cc['games_with_usable_states']+=int(cov.get('usable_states',0)>0);cc['games_with_signals']+=int(bool(rows));rejects.update(rej)
   for k,rr in rows.items():signals[k]+=rr
   if n%100==0:print('PARSE',s,n,'/',len(groups),flush=True)
  coverage[s]=dict(cc);print('COVERAGE',s,dict(cc),flush=True)
 for profile in PROFILES:
  for label,lag,mode,stress in SCENARIOS:
   key=profile+'/'+label;rows=signals[key];record(results,ledgers,'ALL/'+key,rows,stress,False,SPLIT)
   for s in SERIES:
    part=[r for r in rows if r['asset']==s];record(results,ledgers,s+'/'+key,part,stress,False,SPLIT)
   # Pooled non-preseason comparator frozen, not best-league cherry-picking.
   record(results,ledgers,'NO_PRESEASON/'+key,[r for r in rows if r['season_type']!='PRE'],stress,False,SPLIT)
   r=results['ALL/'+key];print('RESULT',key,r['trades'],r['wins'],r['losses'],r['net'],r['drawdown'],flush=True)
 save(P/'results.json',results);save(P/'ledgers.json.gz',ledgers);save(P/'coverage.json',coverage);save(P/'game_coverage.json',gamecov);save(P/'execution_rejections.json',dict(rejects))
if __name__=='__main__':main()
