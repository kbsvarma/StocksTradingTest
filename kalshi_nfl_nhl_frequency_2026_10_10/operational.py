from study import *
RULES={'KXNFLGAME':[('leader',5,'p90'),('leader',5,'wide')],
 'KXNHLGAME':[('leader',5,'p90'),('f55',5,'wide'),('f60',5,'p90')]}
allresults={};allledgers={}
for spread in [.03,.05]:
 candidates=defaultdict(list)
 for s in RULES:
  d=P/'data'/s;groups=defaultdict(list);mult=read(d/'series.json')['fee_multiplier']
  for m in read(d/'markets.json'):groups[m['event_ticker']].append(m)
  for et,ms in groups.items():
   mi=read(d/(et+'_milestone.json'))
   if mi['details']['season_type'] not in ('REG','POST'):continue
   out,_,_=event_candidates(ms,read(d/(et+'_candles.json.gz')),mi,s,read(d/(et+'_espn.json.gz')),mult,spread)
   for k,rr in out.items():candidates[s+'/'+k]+=rr
  print('PARSED',spread,s,flush=True)
 for s,rules in RULES.items():
  for family,minutes,band in rules:
   for label,lag,mode,friction in SCENARIOS:
    rows=[r for r in candidates[s+'/'+band+'/'+('base' if label=='cost2c' else label)] if accepts(r,family,minutes)]
    for part in ['previous_early','previous_late','recent','all_regular']:
     a,b=portfolio([r for r in rows if period(r,part,s)],friction);key=f'spread{round(spread*100)}/{s}/{family}/m{minutes}/{band}/{label}/{part}'
     allresults[key]={**metrics(a),'blocked':b};allledgers[key]=a
 save(P/f'operational_candidates_spread{round(spread*100)}.json.gz',candidates)
save(P/'operational_results.json',allresults);save(P/'operational_ledgers.json.gz',allledgers)
for k,v in allresults.items():
 if '/base/recent' in k:print(k,v['trades'],v['wins'],v['losses'],v['net'])
