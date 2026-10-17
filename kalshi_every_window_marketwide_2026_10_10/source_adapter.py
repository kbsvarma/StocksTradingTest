"""Score-aware NFL/NHL frequency study. Offline research; no order interfaces."""
from common import *
from late import parse_states,at,fee_multiplier_at,clock_seconds
from portfolio import quote,fee,payout,portfolio,metrics,day
from collections import defaultdict,Counter
import bisect,math

BANDS={'p85':(.85,.90),'p90':(.90,.95),'p95':(.95,.98),'wide':(.90,.98)}
SCENARIOS=[('base',30,'open',.01),('cost2c',30,'open',.02),('delay60',30,'close',.01),('source90',90,'open',.01),('both_delays',90,'close',.01)]
FAMILIES={'KXNFLGAME':['leader','f50','f55','f60','multi2','multi3','possession','multi2_possession'],
          'KXNHLGAME':['leader','f50','f55','f60','multi2','multi3']}
SPLIT={'KXNFLGAME':ts('2025-12-01T05:00:00Z'),'KXNHLGAME':ts('2026-02-15T05:00:00Z')}

def convert(y,mi,s):
 """ESPN post-play points and explicit end.team possession; never drive finals."""
 plays=y.get('plays') or [p for dr in (y.get('drives') or {}).get('previous',[]) for p in dr.get('plays',[])]
 periods=defaultdict(list);extras={};counts=Counter();mapping=mi['details']['espn_team_mapping']
 for e in plays:
  if not e.get('wallclock'):counts['missing_wallclock']+=1;continue
  try:
   w=ts(e['wallclock']);per=int(e['period']['number']);seq=float(e['sequenceNumber']);clock=clock_seconds(e.get('clock',{}).get('displayValue'))
   if clock is None:raise ValueError('clock missing')
   if s=='KXNHLGAME':clock=(1200 if per<=3 else 300)-clock
   h=int(e['homeScore']);a=int(e['awayScore'])
  except (KeyError,ValueError,TypeError):counts['malformed_play']+=1;continue
  end_state=e.get('end') or {}
  post=mapping.get(str(end_state.get('team',{}).get('id'))) if s=='KXNFLGAME' and end_state.get('down') in (1,2,3,4) else None
  p={'wall_clock':w,'sequence':seq,'home_points':h,'away_points':a,'clock_seconds':clock,'description':e.get('text',''),'wall_clock_source':'ESPN_event_time'}
  periods[per].append(p);extras[(w,seq)]={'post_possession':post,'modified':e.get('modified'),'espn_play_id':e.get('id')}
 return {'pbp':{'periods':[{'period_number':k,'events':v} for k,v in periods.items()]}},extras,dict(counts)

def prepare(ms,cm,mi,s,y):
 # Historical endpoint uses decimal-dollar strings without `_dollars` keys;
 # older integer representations are cents. Keep raw response files intact.
 for bars in cm.values():
  for c in bars:
   for field in ['yes_bid','yes_ask']:
    for phase in ['open','close']:
     if phase+'_dollars' not in c.get(field,{}):
      val=c.get(field,{}).get(phase)
      if val is not None:c[field][phase+'_dollars']=str(float(val)/100 if isinstance(val,int) else val)
 pb,extras,counts=convert(y,mi,s);states,end,cov=parse_states(pb,mi,s)
 for r in states:r.update(extras.get((r['wall'],r['order']),{}))
 counts.update(cov);stamps=[r['wall'] for r in states];home=mi['details']['home_team_id'];away=mi['details']['away_team_id']
 teammap={m['ticker']:next(iter(m['custom_strike'].values())) for m in ms}
 csmap={m['ticker']:{c['end_period_ts']:c for c in cm.get(m['ticker'],[])} for m in ms}
 for cs in csmap.values():
  for t,c in cs.items():c['_volume10']=sum(float(cs.get(tt,{}).get('volume_fp') or cs.get(tt,{}).get('volume') or 0) for tt in range(t-540,t+60,60))
 pre=(ts(mi['start_date'])-1800)//60*60;pm={};pexact={};page={}
 for m in ms:
  cs=csmap[m['ticker']];eligible=[t for t in cs if pre-300<=t<=pre and quote(cs[t]) and quote(cs[t])[1]-quote(cs[t])[0]<=.02000001]
  if eligible:
   tt=max(eligible);pm[teammap[m['ticker']]]=sum(quote(cs[tt]))/2;page[teammap[m['ticker']]]=pre-tt
  q=quote(cs.get(pre))
  if q and q[1]-q[0]<=.02000001:pexact[teammap[m['ticker']]]=sum(q)/2
 complementary=len(ms)==2 and set(teammap.values())=={home,away}
 if s=='KXNFLGAME':complementary=complementary and all('tie' in m.get('rules_secondary','').lower() and any(text in m.get('rules_secondary','') for text in ['$0.50','50/50']) for m in ms)
 counts['games_with_play_states']=bool(states);counts['games_with_pregame_quote']=bool(pm);counts['games_with_pregame_exact']=bool(pexact)
 return states,stamps,end,home,away,teammap,csmap,pm,pexact,page,complementary,counts

def leader(r,s,t):
 if not r or r['terminal'] or t-r['wall']>120 or r['period']!=(4 if s=='KXNFLGAME' else 3) or r['clock'] is None or not 0<r['clock']<=900:return None
 diff=r['home']-r['away']
 return (diff>0) if diff else None

def event_candidates(ms,cm,mi,s,y,mult,spread_max=.02):
 states,stamps,end,home,away,tm,csmap,pm,pexact,page,comp,cov=prepare(ms,cm,mi,s,y)
 out=defaultdict(list);reject=Counter()
 if not states:return out,cov,dict(reject)
 times=sorted({t for cs in csmap.values() for t in cs if stamps[0]<=t<end})
 for label,lag,mode,friction in SCENARIOS:
  if label=='cost2c':continue # identical candidate path; independent portfolio cost pass below
  for t in times:
   r=at(states,stamps,t,lag);ld=leader(r,s,t)
   if ld is None:continue
   team=home if ld else away
   for band,(low,high) in BANDS.items():
    choices=[]
    for m in ms:
     side='yes' if tm[m['ticker']]==team else 'no'
     if side=='no' and not comp:continue
     cs=csmap[m['ticker']];q=quote(cs.get(t),side)
     if not q:continue
     bid,ask=q
     if not low<=ask<high or ask-bid>spread_max+1e-8 or cs[t]['_volume10']<5:continue
     choices.append((ask,m['ticker'],side,m,cs,bid))
    if not choices:continue
    ask,ticker,side,m,cs,bid=min(choices,key=lambda a:a[:3]);entered=t+(60 if mode=='close' else 3);r2=at(states,stamps,entered,lag)
    if leader(r2,s,entered)!=ld:reject['changed_or_stale_execution_state']+=1;continue
    if entered>=min(end,ts(m['close_time'])):reject['after_game_or_close']+=1;continue
    q2=quote(cs.get(t+60),side,'close' if mode=='close' else 'open')
    if not q2:reject['missing_execution_quote']+=1;continue
    entry=max(ask,q2[1]);cap=min(.98,ask+.01)
    if entry>cap+1e-8:reject['price_over_cap']+=1;continue
    pay=payout(m,side)
    if pay is None or not m.get('settlement_ts') or ts(m['settlement_ts'])<=entered:reject['outcome_or_timestamp']+=1;continue
    prefav={};exactfav={}
    for name,threshold in [('f50',.50),('f55',.55),('f60',.60)]:
     ids=[a for a,p in pm.items() if p>threshold] if name=='f50' else [a for a,p in pm.items() if p>=threshold]
     ids2=[a for a,p in pexact.items() if p>.50] if name=='f50' else [a for a,p in pexact.items() if p>=threshold]
     prefav[name]=(ids==[team]);exactfav[name]=(ids2==[team])
    mf=fee_multiplier_at(s,entered,mult)
    out[band+'/'+label].append({'kind':'sports','asset':s,'event':m['event_ticker'],'ticker':ticker,'side':side,'team':team,'home_team':home,'away_team':away,'t':t,'entered':entered,'entry':entry,'ask':ask,'bid':bid,'cap':cap,'payout':pay,'won':pay==1 if pay in (0,1) else None,'settled':ts(m['settlement_ts']),'close':ts(m['close_time']),'game_end':end,'fee':fee(entry,mf),'fee_multiplier':mf,'source_delay':lag,'score_wall':r2['wall'],'score_age':entered-r2['wall'],'period':r2['period'],'clock':r2['clock'],'home_score':r2['home'],'away_score':r2['away'],'decision_clock':r['clock'],'minimum_lead':min(abs(r['home']-r['away']),abs(r2['home']-r2['away'])),'leader_has_possession':r.get('post_possession')==team and r2.get('post_possession')==team,'prefav':prefav,'exact_prefav':exactfav,'pregame_mid':pm.get(team),'pregame_age':page.get(team),'season_type':mi['details']['season_type'],'source':'ESPN_event_time','source_description':r2['description'],'source_modified':r2.get('modified'),'source_play_id':r2.get('espn_play_id'),'schedule_start':ts(mi['start_date']),'complement_allowed':comp})
 for key,rr in out.items():
  for row in rr:
   row['candidate_id']=s+'/'+key+'/'+row['event']+'/'+str(row['t'])
   row['game_owner']=s+'/'+mi['id']
 return out,cov,dict(reject)

def accepts(r,family,minutes):
 if max(r['clock'],r['decision_clock'])>minutes*60:return False
 if family.startswith('f'):return r['prefav'][family]
 if family in ('multi2','multi2_possession') and r['minimum_lead']<(9 if r['asset']=='KXNFLGAME' else 2):return False
 if family=='multi3' and r['minimum_lead']<(17 if r['asset']=='KXNFLGAME' else 3):return False
 if 'possession' in family and not r['leader_has_possession']:return False
 return True

def period(r,name,s):
 recent=r['schedule_start']>=ts('2026-08-01T04:00:00Z');regular=r['season_type'] in ('REG','POST')
 low,high=(ts('2025-09-01T04:00:00Z'),ts('2026-02-16T05:00:00Z')) if s=='KXNFLGAME' else (ts('2026-01-01T05:00:00Z'),ts('2026-04-01T04:00:00Z'))
 previous=low<=r['schedule_start']<high and regular
 return {'previous':previous,'previous_early':previous and r['schedule_start']<SPLIT[s],
 'previous_late':previous and r['schedule_start']>=SPLIT[s],
 'recent':recent and regular,'recent_preseason':recent and r['season_type']=='PRE','recent_all':recent,'all_regular':regular and (previous or recent)}[name]

def main():
 candidates=defaultdict(list);coverage={};gamecov={};rejects=Counter()
 for s in FAMILIES:
  d=P/'data'/s;groups=defaultdict(list);mult=read(d/'series.json')['fee_multiplier'];cc=Counter()
  for m in read(d/'markets.json'):groups[m['event_ticker']].append(m)
  for n,(et,ms) in enumerate(sorted(groups.items()),1):
   mi=read(d/(et+'_milestone.json'));out,cov,rej=event_candidates(ms,read(d/(et+'_candles.json.gz')),mi,s,read(d/(et+'_espn.json.gz')),mult)
   gamecov[et]={'series':s,'start':mi['start_date'],'season_type':mi['details']['season_type'],**cov,'candidate_states':sum(len(a) for a in out.values())}
   cc.update(cov);cc['games']+=1;rejects.update(rej)
   for k,rr in out.items():candidates[s+'/'+k]+=rr
   if n%50==0:print('PARSE',s,n,'/',len(groups),flush=True)
  coverage[s]=dict(cc)
 save(P/'candidates.json.gz',candidates);save(P/'game_coverage.json',gamecov);save(P/'coverage.json',coverage);save(P/'rejections.json',dict(rejects));replay(candidates)

def replay(candidates):
 results={};ledgers={}
 for s,families in FAMILIES.items():
  for family in families:
   for minutes in [5,10,15]:
    for band in BANDS:
     for label,lag,mode,friction in SCENARIOS:
      rr=[r for r in candidates.get(s+'/'+band+'/'+('base' if label=='cost2c' else label),[]) if accepts(r,family,minutes)]
      for part in ['previous','previous_early','previous_late','recent','recent_preseason','recent_all','all_regular']:
       rows=[r for r in rr if period(r,part,s)];a,blocked=portfolio(rows,friction);key=f'{s}/{family}/m{minutes}/{band}/{label}/{part}';v=metrics(a);v['blocked']=blocked
       v['game_days']=len({day(r['entered']) for r in a});v['first_entry']=min((r['entered'] for r in a),default=None);v['last_entry']=max((r['entered'] for r in a),default=None)
       results[key]=v;ledgers[key]=[{k:z[k] for k in ['candidate_id','net','reserve']} for z in a]
     print('RESULT',s,family,minutes,band,'recent',results[f'{s}/{family}/m{minutes}/{band}/base/recent']['trades'],results[f'{s}/{family}/m{minutes}/{band}/base/recent']['net'],flush=True)
 save(P/'results.json',results);save(P/'ledgers.json.gz',ledgers)
if __name__=='__main__':main()
