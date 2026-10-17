from late import *
from portfolio import metrics,portfolio

signals=defaultdict(list);coverage={};seen=set()
for root in ['earlier','data']:
 d=P/root/'KXMLBGAME';ms=read(d/'markets.json');mult=read(d/'series.json')['fee_multiplier'];groups=defaultdict(list);ct=Counter()
 for m in ms:groups[m['event_ticker']].append(m)
 for n,(et,items) in enumerate(sorted(groups.items()),1):
  if et in seen:continue
  seen.add(et);ct['events']+=1;mp=d/(et+'_milestone.json');cp=d/(et+'_candles.json.gz');pp=d/(et+'_plays.json.gz')
  if not all(f.exists() for f in [mp,cp,pp]):ct['missing_files']+=1;continue
  mile=read(mp);rows,cov,rej=event_rows(items,read(cp),mile,'KXMLBGAME',read(pp),None,mult)
  ct['games_with_states']+=int(cov['usable_states']>0);ct['games_with_signals']+=int(bool(rows))
  for k,rr in rows.items():signals[k]+=rr
  if n%100==0:print('EXT_PARSE',root,n,'/',len(groups),flush=True)
 coverage[root]=dict(ct)
results={};ledgers={};cut=ts('2026-08-15T04:00:00Z')
for key,rows in signals.items():
 stress=.02 if key.endswith('/cost2c') else .01
 for period in ['earlier','recent','full']:
  selected=[r for r in rows if period=='full' or (r['t']<cut if period=='earlier' else r['t']>=cut)]
  k=period+'/'+key;r=record(results,ledgers,k,selected,stress,False,cut);print('EXT_RESULT',k,r['trades'],r['wins'],r['losses'],r['net'],r['drawdown'],flush=True)
save(P/'extended_results.json',results);save(P/'extended_ledgers.json.gz',ledgers);save(P/'extended_coverage.json',coverage)
