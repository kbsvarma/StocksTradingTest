"""Causal replay of the frozen August teacher set; Kalshi execution only."""
from study import *
from teacher_replay import ALIASES,normalized
from collections import defaultdict,Counter
from datetime import datetime
import re,sys
D=P/'teachers';START=ts('2026-08-15T04:00:00Z');END=ts('2026-10-10T04:00:00Z');SPLIT=ts('2026-09-12T04:00:00Z')

def build():
 aliases=defaultdict(set)
 for name,value in ALIASES.items():
  parts=name.split()
  for v in (name,value,parts[-1],' '.join(parts[-2:])):aliases[normalized(v)].add(normalized(value))
 def norm(s):
  s=normalized(s);values=aliases.get(s)
  return next(iter(values)) if values and len(values)==1 else s
 events={};mapping=defaultdict(list)
 for series in ('KXMLBGAME','KXWNBAGAME'):
  d=P/'sports'/series;by=defaultdict(list)
  for m in read(d/'markets.json'):by[m['event_ticker']].append(m)
  mult=read(d/'series.json')['fee_multiplier']
  for et,ms in by.items():
   mp=d/(et+'_milestone.json')
   if not mp.exists() or len(ms)!=2:continue
   mile=read(mp);names={norm(m['yes_sub_title']):m for m in ms}
   if len(names)!=2:continue
   st=ts(mile['start_date']);events[et]={'markets':names,'milestone':mile,'series':series,'mult':mult};mapping[(series,day(st),tuple(sorted(names)))].append(et)
 frozen=read(D/'frozen_teachers.json');rows=[];audit=Counter();examples=[]
 for leader in frozen['leaders']:
  wallet=leader['proxyWallet']
  for r in read(D/(wallet+'.json.gz')):
   audit['public_buys']+=1;slug=r.get('eventSlug','');series='KXMLBGAME' if slug.startswith('mlb-') else 'KXWNBAGAME' if slug.startswith('wnba-') else None
   if not series:audit['other_family']+=1;continue
   # Only simple two-team winner titles; never spread/total/inning/player markets.
   title=r.get('title','');parts=re.split(r'\s+vs\.?\s+',title,flags=re.I)
   if len(parts)!=2 or any(w in title.lower() for w in ('o/u','spread','handicap','inning','half','hits','runs','total','points','rebounds','assists')):audit['not_simple_winner']+=1;continue
   date=re.search(r'20\d\d-\d\d-\d\d',slug)
   if not date:audit['no_slug_date']+=1;continue
   pair=tuple(sorted(norm(x) for x in parts));ets=mapping.get((series,date[0],pair),[])
   if len(ets)!=1:audit['ambiguous_or_unmatched_fixture']+=1;continue
   et=ets[0];ev=events[et];m=ev['markets'].get(norm(r['outcome']))
   if m is None:audit['unmatched_outcome']+=1;continue
   tx=int(r['timestamp']);start=ts(ev['milestone']['start_date']);end=ts(ev['milestone']['end_date']) if ev['milestone'].get('end_date') else ts(m['close_time'])
   if not start-36*3600<=tx<end:audit['time_mismatch']+=1;continue
   x={'event':et,'ticker':m['ticker'],'asset':series,'wallet':wallet,'teacher':leader.get('userName'),'timestamp':tx,'notional':float(r.get('usdcSize') or float(r['price'])*float(r['size'])),'polymarket_price':float(r['price']),'polymarket_slug':r.get('slug'),'transaction':r.get('transactionHash')};rows.append(x);audit['matched_buys']+=1
   if len(examples)<10:examples.append({'poly_title':title,'poly_outcome':r['outcome'],'poly_slug':slug,'kalshi_ticker':m['ticker'],'start':start,'trade':tx})
 signals=defaultdict(list);notionals=defaultdict(lambda:defaultdict(float));wallets=defaultdict(dict);conv_seen=set();cons_seen=set()
 grouped=defaultdict(list)
 for x in rows:grouped[x['timestamp']].append(x)
 for timestamp,group in sorted(grouped.items()):
  previous={x['event']:set(wallets[x['event']]) for x in group}
  for x in group:
   key=(x['wallet'],x['event']);notionals[key][x['ticker']]+=x['notional'];wallets[x['event']].setdefault(x['wallet'],set()).add(x['ticker'])
  for key in sorted({(x['wallet'],x['event']) for x in group}):
   ns=notionals[key];winner=max(ns,key=ns.get);other=sum(ns.values())-ns[winner]
   records=[x for x in group if (x['wallet'],x['event'])==key and x['ticker']==winner]
   if records and key not in conv_seen and ns[winner]>=100 and (other==0 or ns[winner]/other>=4):signals['teacher_conviction'].append(dict(records[0]));conv_seen.add(key)
  for event in sorted({x['event'] for x in group}):
   ew=wallets[event];sides={t for vv in ew.values() for t in vv};new=[x for x in group if x['event']==event and x['wallet'] not in previous[event]]
   if event not in cons_seen and new and len(ew)>=2:
    cons_seen.add(event)
    # Timestamp ties have no reliable public sub-second ordering. Require the
    # smallest potential second-teacher trade to clear the notional threshold.
    if len(sides)==1 and min(x['notional'] for x in new)>=500:signals['teacher_consensus'].append(dict(new[0]))
 save(D/'matched_activity.json.gz',rows);save(D/'signals.json',dict(signals));save(D/'matching_audit.json',dict(counts=dict(audit),examples=examples));return events,signals

def fetch(events,signals):
 from common import get
 needed=defaultdict(set)
 for ss in signals.values():
  for x in ss:
   for delay in (60,120,300):
    t=math.ceil((x['timestamp']+delay)/60)*60
    if START<=t<END:needed[x['event']].update(range(t-600,t+121,60))
 jobs=[];out=defaultdict(lambda:defaultdict(dict));tickevent={}
 for et,times in sorted(needed.items()):
  if (D/(et+'_quotes.json.gz')).exists():continue
  items=list(events[et]['markets'].values());cl=min(ts(m['close_time']) for m in items);times=sorted(t for t in times if t<cl)
  ticks=[m['ticker'] for m in items]
  for tick in ticks:tickevent[tick]=et
  if not times:save(D/(et+'_quotes.json.gz'),{});continue
  lo=hi=times[0]
  for t in times[1:]:
   if t-hi>1800 or t-lo>2000*60:jobs.append((lo-60,hi+60,ticks));lo=t
   hi=t
  jobs.append((lo-60,hi+60,ticks))
 batches=[];ticks=set();lo=hi=0
 for a,b,jticks in sorted(jobs):
  nlo=min(a,lo) if ticks else a;nhi=max(b,hi) if ticks else b;nt=ticks|set(jticks)
  if ticks and (len(nt)>100 or len(nt)*((nhi-nlo)//60+2)>9000):batches.append((lo,hi,sorted(ticks)));ticks=set();nlo=a;nhi=b;nt=set(jticks)
  ticks=nt;lo=nlo;hi=nhi
 if ticks:batches.append((lo,hi,sorted(ticks)))
 print('TEACHER_BATCHES',len(jobs),'intervals',len(batches),'requests',flush=True)
 for i,(lo,hi,ticks) in enumerate(batches):
  raw=get('/markets/candlesticks',{'market_tickers':','.join(ticks),'start_ts':lo,'end_ts':hi,'period_interval':1})
  for r in raw.get('markets',[]):
   et=tickevent[r['market_ticker']]
   for c in r['candlesticks']:out[et][r['market_ticker']][c['end_period_ts']]=c
  if i%25==0:print('TEACHER_BATCH',i+1,'/',len(batches),flush=True)
 for et in set(tickevent.values()):save(D/(et+'_quotes.json.gz'),{k:list(v.values()) for k,v in out[et].items()})
 save(D/'quote_receipt.json',{'events':len(needed),'batch_requests':len(batches)})

def analyze(events,signals):
 results={};ledgers={};counts={}
 for prof,ss in signals.items():
  for delay in (60,120,300):
   for label,stress,mode in SCENARIOS:
    candidates=[];ct=Counter()
    for x in ss:
     t=math.ceil((x['timestamp']+delay)/60)*60
     if not START<=t<END:continue
     ct['signals']+=1;ev=events[x['event']];m=next(m for m in ev['markets'].values() if m['ticker']==x['ticker']);file=D/(x['event']+'_quotes.json.gz')
     if not file.exists():ct['no_quote_file']+=1;continue
     cs={int(c['end_period_ts']):c for c in read(file).get(x['ticker'],[])};q=quote(cs.get(t))
     if q is None:ct['missing_quote']+=1;continue
     b,a=q
     if (prof=='teacher_consensus' and not .40<=a<=.60) or not .05<=a<.95 or a-b>.02000001:ct['price_or_spread']+=1;continue
     if sum(float(c.get('volume_fp') or 0) for tt,c in cs.items() if t-600<tt<=t)<5:ct['volume']+=1;continue
     cl=ts(m['close_time']);ge=ts(ev['milestone']['end_date']) if ev['milestone'].get('end_date') else cl
     s=dict(x,kind='sports',side='yes',profile=prof,t=t,ask=a,bid=b,cap=min(a+.01,.95),close=cl,game_end=ge,source_ts=x['timestamp'],delay=delay,_cs=cs,_market=m,_mult=ev['mult']);r=execute(s,mode)
     if r:candidates.append(r)
     else:ct['execution']+=1
    key=prof+'_'+str(delay)+'/'+label;m=record(results,ledgers,key,candidates,stress,False,SPLIT);counts[key]=dict(ct)
    for asset in ('KXMLBGAME','KXWNBAGAME'):record(results,ledgers,asset+'/'+key,[r for r in candidates if r['asset']==asset],stress,False,SPLIT)
    print('TEACHER_RESULT',key,{k:v for k,v in m.items() if k in ('trades','wins','losses','net','drawdown','weekly')},flush=True)
 save(P/'teachers_results.json',results);save(P/'teachers_ledgers.json.gz',ledgers);save(P/'teachers_coverage.json',counts)
if __name__=='__main__':
 events,signals=build();print('MATCHED',{k:len(v) for k,v in signals.items()},flush=True);fetch(events,signals);analyze(events,signals)
