"""Frozen replication and distinct sports entry mechanisms, with cost sensitivity."""
import sys,math,json,gzip,statistics
from collections import defaultdict,Counter
from datetime import datetime
from zoneinfo import ZoneInfo
from common import *
ET=ZoneInfo('America/New_York')
def day(t):return datetime.fromtimestamp(t,ET).date().isoformat()
def quote(c,phase='close'):
 try:
  b=float(c['yes_bid'][phase+'_dollars']);a=float(c['yes_ask'][phase+'_dollars'])
  return (b,a) if 0<=b<=a<=1 else None
 except (KeyError,TypeError,ValueError):return None
def sq(q,side):return q if side=='yes' else (1-q[1],1-q[0])
def fee(p,m):return math.ceil((.07*m*5*p*(1-p)-1e-12)*10000)/10000
def metrics(rs):
 ds=defaultdict(float);eq=peak=dd=0
 for r in sorted(rs,key=lambda x:(x['settled'],x['event'])):
  ds[day(r['settled'])]+=r['net'];eq+=r['net'];peak=max(peak,eq);dd=max(dd,peak-eq)
 return {'trades':len(rs),'wins':sum(r['won'] for r in rs),'losses':sum(not r['won'] for r in rs),'net':round(eq,5),'drawdown':round(dd,5),'daily':dict(ds),'without_best3':round(eq-sum(sorted([r['net'] for r in rs],reverse=True)[:3]),5)}
def portfolio(rows,stress,enforce=True):
 cash=100.;active=[];accepted=[];realized=defaultdict(float);seen=set();blocked=Counter()
 for r in sorted(rows,key=lambda x:(x['entered'],x['asset'],x['event'],x['entry'],x['ticker'])):
  for a in sorted(active,key=lambda x:x['settled']):
   if a['settled']<=r['entered']:
    cash+=5*a['won'];realized[day(a['settled'])]+=a['net']
  active=[a for a in active if a['settled']>r['entered']]
  if r['event'] in seen:continue
  reserve=5*max(.95,r['entry']+.02);risk=sum(x['reserve'] for x in active)+reserve
  cost=5*r['entry']+r['fee']+5*stress
  if enforce:
   why=''
   if r['category']=='weather' and any(x['asset']==r['asset'] for x in active):why='city_pending'
   elif len(active)>=5:why='five_positions'
   elif risk>25+1e-8:why='exposure'
   elif realized[day(r['entered'])]-risk < -20-1e-8:why='daily_including_open'
   elif cash-cost<2:why='cash'
   if why:blocked[why]+=1;continue
  out=dict(r,net=5*(int(r['won'])-r['entry'])-r['fee']-5*stress,reserve=reserve)
  cash-=cost;active.append(out);accepted.append(out);seen.add(r['event'])
 return accepted,dict(blocked)
def make(m,series,t,side,ask,cs,mode,category,profile):
 nxt=cs.get(t+60);q=quote(nxt,'close' if mode=='delay60' else 'open')
 if q is None:return None
 entry=max(ask,sq(q,side)[1]); cap=min(ask+.01,.99)
 if entry>cap+1e-8 or entry<=0 or t+60>=ts(m['close_time']):return None
 # Earliest possible execution is just after a completed decision minute.
 entered=t+60 if mode=='delay60' else t+3
 settled=ts(m['settlement_ts']);assert settled>entered
 return dict(series=series,asset=series,category=category,profile=profile,event=m['event_ticker'],ticker=m['ticker'],t=t,entered=entered,close=ts(m['close_time']),settled=settled,side=side,entry=entry,ask=ask,cap=cap,won=m['result']==side,fee=fee(entry,m['_mult']))
def generic(ms,cmap,series,mode,start,profile):
 prepared={m['ticker']:{c['end_period_ts']:c for c in cmap.get(m['ticker'],[])} for m in ms}
 weather=series.startswith('KXTEMP');rows=[]
 times=sorted({t for cs in prepared.values() for t in cs})
 close=min(ts(m['close_time']) for m in ms)
 for t in times:
  if weather:
   if not 15<= (close-t)/60 <=45:continue
   lo,hi,hold=(.85,.93,5) if profile=='weather85' else (.93,.98,5)
  elif profile.startswith('pregame'):
   if t!=((start-1800)//60)*60:continue
   lo,hi,hold=(.70,.90,1) if profile=='pregame_favorite' else (.10,.30,1)
  else:
   if t<start or t>start+6*3600:continue
   lo,hi,hold=(.95,.98,5) if profile=='held95' else (.97,.99,10)
  choices=[]
  for m in ms:
   if m.get('result') not in ('yes','no'):continue
   cs=prepared[m['ticker']];q=quote(cs.get(t))
   if q is None:continue
   # Mirror ownership is enforced by event. Select the cheapest equivalent side.
   sides=('yes','no') if not weather or len(ms)==1 else ('yes','no')
   for side in sides:
    bid,ask=sq(q,side)
    if not lo<=ask<hi or ask-bid>.02000001:continue
    if profile.startswith('pregame') and len(ms)>3:continue
    good=True
    for back in range(hold):
     qq=quote(cs.get(t-back*60))
     if qq is None:good=False;break
     bb,aa=sq(qq,side)
     if weather:
      # Original weather hypothesis holds the favorite side, not the price band.
      if bb+aa<=1:good=False;break
     elif not lo<=aa<hi or aa-bb>.02000001:good=False;break
    if not good:continue
    if sum(float(c.get('volume_fp') or 0) for tt,c in cs.items() if t-600<=tt<=t)<5:continue
    x=make(m,series,t,side,ask,cs,mode,'weather' if weather else 'sports',profile)
    if x:choices.append(x)
  # Only one selected strike per minute; later minutes remain available if risk blocks.
  if choices:rows.append(min(choices,key=lambda x:(x['entry'],x['ticker'],x['side'])))
 return rows
def tennis(ms,cmap,plays,mode):
 rows=[];byid={m['custom_strike'].get('tennis_competitor'):m for m in ms if m.get('result') in ('yes','no')}
 for period in plays.get('pbp',{}).get('periods',[]):
  home=period.get('home_competitor_id');away=period.get('away_competitor_id')
  for ev in period.get('events',[]):
   if ev.get('event_type')!='point' or not ev.get('wall_clock'):continue
   server=ev.get('server')
   if server not in byid or server not in (home,away):continue
   own,other=('home','away') if server==home else ('away','home')
   try:sets=int(ev[own+'_sets']);opp_sets=int(ev[other+'_sets']);games=int(ev[own+'_games']);opp_games=int(ev[other+'_games']);points=int(ev[own+'_score']);opp_points=int(ev[other+'_score'])
   except (KeyError,TypeError,ValueError):continue
   if sets!=1 or opp_sets not in (0,1):continue
   if not ((games==5 and 0<=opp_games<=4) or (games==6 and opp_games==5)):continue
   # Skip match point and ambiguous advantage rows, never completed games/sets.
   if points not in (0,15,30) or opp_points not in (0,15,30,40):continue
   t=math.ceil(int(ev['wall_clock'])/60)*60
   for m,side in [(byid[server],'yes')]+[(v,'no') for k,v in byid.items() if k!=server]:
    cs={c['end_period_ts']:c for c in cmap.get(m['ticker'],[])};q=quote(cs.get(t))
    if q is None:continue
    bid,ask=sq(q,side)
    if not .95<=ask<.97 or ask-bid>.02000001:continue
    x=make(m,'KXWTAMATCH',t,side,ask,cs,mode,'sports','serving_match')
    if x:x.update(score_timestamp=int(ev['wall_clock']),sets=sets,games=games,opp_games=opp_games,server=server);rows.append(x)
 return sorted(rows,key=lambda x:(x['entered'],x['entry'],x['ticker']))
def main():
 results={};ledgers={};coverage={};pools=defaultdict(list)
 for dest in sorted((P/'replays').iterdir()):
  if not (dest/'receipt.json').exists():continue
  s=dest.name;ms=read(dest/'markets.json');meta=read(dest/'series.json');events=defaultdict(list)
  for m in ms:m['_mult']=meta['fee_multiplier'];events[m['event_ticker']].append(m)
  coverage[s]={'events':len(events),'markets':len(ms),'candles_present':0,'plays_present':0,'start_missing':0,'nonbinary_results':sum(m.get('result') not in ('yes','no') for m in ms)}
  profiles=('weather85','weather93') if s.startswith('KXTEMP') else ('pregame_favorite','pregame_underdog','held95','held97')
  if s=='KXWTAMATCH':profiles+=('serving_match',)
  for profile in profiles:
   for mode,stress,label in [('opening',.01,'fees1c'),('opening',.02,'fees2c'),('delay60',.01,'delay1c')]:
    allrows=[]
    for e,items in events.items():
     cp=dest/(e+'_candles.json.gz')
     if not cp.exists():continue
     cm=read(cp);start=0
     if not s.startswith('KXTEMP'):
      miles=read(dest/(e+'_milestones.json'));main=[x for x in miles if (x.get('details') or {}).get('main_game_event_ticker')==e]
      if len(main)!=1:continue
      start=ts(main[0]['start_date'])
     if profile=='serving_match':
      pp=dest/(e+'_plays.json.gz')
      if not pp.exists() or str(main[0].get('details',{}).get('best_of'))!='3':continue
      rr=tennis(items,cm,read(pp),mode)
     else:rr=generic(items,cm,s,mode,start,profile)
     allrows+=rr
    key=s+'/'+profile+'/'+label;pools[(profile,label)]+=allrows;results[key]={}
    for period,lo,hi in [('full',START,END),('first_week',START,START+7*86400),('second_week',START+7*86400,END)]:
     select=[x for x in allrows if lo<=x['close']<hi]
     acc,blocked=portfolio(select,stress);unc,_=portfolio(select,stress,False)
     results[key][period]={'portfolio':metrics(acc),'unconstrained':metrics(unc),'blocked':blocked};ledgers[key+'/'+period]=acc
    print(key,results[key]['full']['portfolio'],flush=True)
  coverage[s]['candles_present']=len(list(dest.glob('*_candles.json.gz')));coverage[s]['plays_present']=len(list(dest.glob('*_plays.json.gz')));coverage[s]['start_missing']=len(list(dest.glob('*_skip.json')))
 for (profile,label),rows in pools.items():
  stress=.02 if label=='fees2c' else .01;acc,blocked=portfolio(rows,stress);results['ALL/'+profile+'/'+label]={'full':{'portfolio':metrics(acc),'blocked':blocked}};ledgers['ALL/'+profile+'/'+label]=acc
 save(P/'replay_results.json',results);save(P/'replay_ledgers.json.gz',ledgers);save(P/'replay_coverage.json',coverage)
if __name__=='__main__':main()
