from study import *
from datetime import timedelta
from zoneinfo import ZoneInfo
import numpy as np

results=read(P/'results.json');led=read(P/'ledgers.json.gz');candidates=read(P/'candidates.json.gz');ix={r['candidate_id']:r for rr in candidates.values() for r in rr};gc=read(P/'game_coverage.json')
RULES={'NFL90':'KXNFLGAME/leader/m5/p90','NFL98':'KXNFLGAME/leader/m5/wide','NFL_old':'KXNFLGAME/f60/m5/p90','NHL55':'KXNHLGAME/f55/m5/wide','NHL_leader':'KXNHLGAME/leader/m5/p90','NHL_old':'KXNHLGAME/f60/m5/p90'}
def expand(key):return [{**ix[x['candidate_id']],**x} for x in led[key]]
def tuesday_week(t):
 dt=datetime.fromisoformat(day(t)).date();return (dt-timedelta(days=(dt.weekday()-1)%7)).isoformat()
def digest(rows):
 m=metrics(rows);weeks=defaultdict(list);months=defaultdict(list)
 for r in rows:weeks[tuesday_week(r['entered'])].append(r);months[day(r['entered'])[:7]].append(r)
 m.update(profitable=sum(r['net']>0 for r in rows),losing=sum(r['net']<0 for r in rows),game_days=len({day(r['entered']) for r in rows}),monthly={k:metrics(v) for k,v in sorted(months.items())},tuesday_weeks={k:metrics(v) for k,v in sorted(weeks.items())},first_date=min((day(r['entered']) for r in rows),default=None),last_date=max((day(r['entered']) for r in rows),default=None),loss_details=[r for r in rows if r['net']<0])
 n=len(rows);w=sum(r['won'] is True for r in rows)
 if n:
  z=1.95996398454;p=w/n;center=(p+z*z/(2*n))/(1+z*z/n);rad=z*math.sqrt(p*(1-p)/n+z*z/(4*n*n))/(1+z*z/n);m['wilson95']=[center-rad,center+rad]
 return m
def marked(rows):
 if not rows:return {}
 books={};times=set()
 for r in rows:
  d=P/'data'/r['asset'];cm=read(d/(r['event']+'_candles.json.gz'));bars=cm[r['ticker']]
  for c in bars:
   for f in ['yes_bid','yes_ask']:
    if 'close_dollars' not in c.get(f,{}):
     v=c.get(f,{}).get('close')
     if v is not None:c[f]['close_dollars']=str(v/100 if isinstance(v,int) else v)
  q=sorted((c['end_period_ts'],quote(c,r['side'])[0]) for c in bars if quote(c,r['side']));books[r['candidate_id']]=([t for t,b in q],[b for t,b in q])
  times.update(range(r['entered']//60*60,r['settled']+60,60));times.update([r['entered'],r['settled']])
 peak=100.;dd=0;missing=0;maxage=0
 for t in sorted(times):
  active=[r for r in rows if r['entered']<=t<r['settled']];eq=100+sum(r['net'] for r in rows if r['settled']<=t)-sum(r['reserve'] for r in active);valid=True
  for r in active:
   stamps,bids=books[r['candidate_id']];j=bisect.bisect_right(stamps,t)-1
   if j<0:missing+=1;valid=False;continue
   age=t-stamps[j];maxage=max(maxage,age)
   if age>120:missing+=1;valid=False
   eq+=5*bids[j]
  if valid:peak=max(peak,eq);dd=max(dd,peak-eq)
 return {'bid_marked_drawdown':round(dd,5),'missing_or_stale_held_marks':missing,'max_quote_age_seconds':maxage,'caveat':'Sampled bids only, no verified exit depth/fees or intraminute/gap extreme.'}

out={'rules':{},'universe':{},'comparisons':{},'joint':{}}
for name,rule in RULES.items():
 v={}
 for part in ['previous_early','previous_late','previous','recent','recent_preseason','all_regular']:
  rows=expand(rule+'/base/'+part);v[part]=digest(rows)
 for part in ['recent','all_regular']:v[part]['marks']=marked(expand(rule+'/base/'+part))
 out['rules'][name]=v
 print(name,[(part,v[part]['trades'],v[part]['wins'],v[part]['losses'],v[part]['net']) for part in ['previous','recent','all_regular']],flush=True)
for s in FAMILIES:
 games=[v for v in gc.values() if v['series']==s and ts(v['start'])>=ts('2026-08-01T04:00:00Z') and v['season_type'] in ('REG','POST')]
 dates=sorted({day(ts(v['start'])) for v in games});out['universe'][s]={'recent_games':len(games),'recent_games_with_states':sum(bool(v['games_with_play_states']) for v in games),'recent_game_days':len(dates),'first_game':dates[0],'last_game':dates[-1],'calendar_days':(datetime.fromisoformat(dates[-1])-datetime.fromisoformat(dates[0])).days+1,'game_dates':dates}
for name in ['NFL90','NFL98','NHL55','NHL_leader']:
 old='NFL_old' if name.startswith('NFL') else 'NHL_old';a=expand(RULES[old]+'/base/recent');b=expand(RULES[name]+'/base/recent');am={r['game_owner']:r for r in a};bm={r['game_owner']:r for r in b};common=set(am)&set(bm)
 out['comparisons'][name]={'old_trades':len(a),'new_trades':len(b),'net_additional_trades':len(b)-len(a),'added_games':len(set(bm)-set(am)),'removed_games':len(set(am)-set(bm)),'common_games':len(common),'common_different_entry':sum(am[k]['candidate_id']!=bm[k]['candidate_id'] for k in common),'net_improvement':round(sum(r['net'] for r in b)-sum(r['net'] for r in a),5)}
for label,lag,mode,friction in SCENARIOS:
 joined=[]
 for name in ['NFL90','NHL55']:
  s,f,m,band=RULES[name].split('/');rr=[r for r in candidates[s+'/'+band+'/'+('base' if label=='cost2c' else label)] if accepts(r,f,int(m[1:])) and period(r,'all_regular',s)];joined+=rr
 for part in ['all','recent','past_validation']:
  rr=[r for r in joined if part=='all' or (r['schedule_start']>=ts('2026-08-01T04:00:00Z') if part=='recent' else period(r,'previous_late',r['asset']))]
  a,b=portfolio(rr,friction);out['joint'][label+'/'+part]={**digest(a),'blocked':b}

# An explicitly secondary continuous portfolio of NFL validation + recent,
# preserving one $100 bankroll through the intervening summer with no trades.
base=[]
for sc,_,_,friction in SCENARIOS:
 rr=[r for r in candidates['KXNFLGAME/p90/'+('base' if sc=='cost2c' else sc)] if accepts(r,'leader',5) and (period(r,'previous_late','KXNFLGAME') or period(r,'recent','KXNFLGAME'))]
 a,b=portfolio(rr,friction);out.setdefault('NFL_validation_plus_recent',{})[sc]={**digest(a),'blocked':b}
 if sc=='base':base=a
ws=defaultdict(float)
for r in base:ws[tuesday_week(r['entered'])]+=r['net']
vals=np.array(list(ws.values()));rng=np.random.default_rng(10102026)
samples=vals[rng.integers(0,len(vals),size=(20000,len(vals)))].sum(axis=1)
out['NFL_validation_plus_recent']['base']['active_week_bootstrap95']=np.quantile(samples,[.025,.975]).tolist()
out['uncertainty']='Historical revision/arrival and fill assumptions remain; model selection not accounted for by Wilson/bootstrap intervals. Recent data reused, not untouched prospective validation.'
if (P/'operational_ledgers.json.gz').exists():
 op=read(P/'operational_ledgers.json.gz');out['NFL_spread5']={}
 for part in ['previous_early','previous_late','recent','all_regular']:
  rr=op['spread5/KXNFLGAME/leader/m5/p90/base/'+part];out['NFL_spread5'][part]=digest(rr)
  if part in ['recent','all_regular']:out['NFL_spread5'][part]['marks']=marked(rr)
if (P/'period_spread5_ledgers.json.gz').exists():
 per=read(P/'period_spread5_ledgers.json.gz');rr=per['wide90/lead1/m5/base/ALL/REG'];out['NHL_period_spread5']=digest(rr)
 out['NHL_period_spread5']['per_period']={str(n):metrics([r for r in rr if r['period']==n]) for n in [1,2,3]}
 out['NHL_period_spread5']['unique_games']=len({r['game_owner'].split('/period')[0] for r in rr})
save(P/'evaluation.json',out)
print('UNIVERSE',out['universe']);print('COMPARISON',out['comparisons']);print('NFL OCT',out['rules']['NFL90']['recent']['monthly'].get('2026-10'));print('NFL WEEKS',[(k,v['trades'],v['net']) for k,v in out['rules']['NFL90']['recent']['tuesday_weeks'].items()])
