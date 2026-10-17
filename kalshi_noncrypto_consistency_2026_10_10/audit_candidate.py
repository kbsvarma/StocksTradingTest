"""Independent fresh API checks and input coverage for the selected sports follow-up."""
from common import *
from study import quote
from collections import defaultdict, Counter
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import random
ET=ZoneInfo('America/New_York')
ll=read(P/'extended_sports_ledgers.json.gz')['ALL/pregame70/base']
rng=random.Random(101026);chosen=[]
for asset in ('KXMLBGAME','KXWNBAGAME'):
 for before,won in ((True,True),(True,False),(False,False)):
  rr=[r for r in ll if r['asset']==asset and (r['entered']<ts('2026-08-15T04:00:00Z'))==before and r['won']==won]
  if rr:chosen.append(rng.choice(rr))
checks=[]
for r in chosen:
 historical=r['settled']<ts('2026-08-11T00:00:00Z')
 if historical:
  z=get('/historical/markets/'+r['ticker']+'/candlesticks',{'start_ts':r['t']-60,'end_ts':r['t']+120,'period_interval':1},cache=False);cs=z.get('candlesticks',[])
  for c in cs:
   for f in ('yes_bid','yes_ask'):
    for phase in ('open','close'):
     if phase+'_dollars' not in c.get(f,{}):c.setdefault(f,{})[phase+'_dollars']=c[f].get(phase)
 else:
  z=get('/markets/candlesticks',{'market_tickers':r['ticker'],'start_ts':r['t']-60,'end_ts':r['t']+120,'period_interval':1},cache=False);cs=next((x['candlesticks'] for x in z['markets'] if x['market_ticker']==r['ticker']),[])
 cs={c['end_period_ts']:c for c in cs};q=quote(cs.get(r['t']),r['side']);n=quote(cs.get(r['t']+60),r['side'],'open')
 checks.append({'ticker':r['ticker'],'t':r['t'],'side':r['side'],'ask':r['ask'],'entry':r['entry'],'fresh_signal_quote':q,'fresh_execution_quote':n,'matches':bool(q and n and abs(q[1]-r['ask'])<1e-8 and abs(max(q[1],n[1])-r['entry'])<1e-8)})
coverage={}
for series in ('KXMLBGAME','KXWNBAGAME'):
 events=defaultdict(list);allmiles={};files={}
 for root in ('sports_earlier','sports'):
  d=P/root/series
  for m in read(d/'markets.json'):events[m['event_ticker']].append(m)
  for f in d.glob('*_milestone.json'):allmiles[f.name[:-len('_milestone.json')]]=read(f)
  for f in d.glob('*_candles.json.gz'):files[f.name[:-len('_candles.json.gz')]]=f
 # Earlier milestones also identify records just outside the main-period margin.
 for m in read(P/'sports_earlier'/series/'milestones.json'):
  et=m.get('details',{}).get('main_game_event_ticker')
  if et and et not in allmiles:allmiles[et]=m
 counts=Counter();unknown=[]
 for event in events:
  if event not in allmiles:unknown.append(event);counts['unknown_schedule']+=1;continue
  st=ts(allmiles[event]['start_date'])
  if not ts('2026-07-01T04:00:00Z')<=st<ts('2026-10-10T04:00:00Z'):counts['outside_period']+=1;continue
  counts['in_period_games']+=1
  if event not in files:counts['missing_candle_file']+=1
  else:
   cs=read(files[event]);t=((st-1800)//60)*60
   if any(any(c['end_period_ts']==t and quote(c) is not None for c in cc) for cc in cs.values()):counts['pregame_quote_present']+=1
   else:counts['missing_pregame_quote']+=1
 coverage[series]={'counts':dict(counts),'unknown_events':unknown}
print('FRESH_QUOTE_CHECKS',checks);print('COVERAGE',coverage)
save(P/'candidate_input_audit.json',{'checked_utc':datetime.now(timezone.utc).isoformat(),'fresh_quote_checks':checks,'coverage':coverage})
assert all(x['matches'] for x in checks)
