from study import *
from decimal import Decimal as D
r=json.load(open(P/'results.json'));ls=json.load(gzip.open(P/'ledgers.json.gz','rt'));n=0
for k,rows in ls.items():
 assert abs(sum(x['net'] for x in rows)-r[k]['net'])<1e-8
 prev=0;capital=100.
 lev=float(k.split('/L')[1].split('/')[0]);slip=float(k.split('/slip')[1].split('/')[0]);delay=int(k.split('/delay')[1].split('/')[0])
 for x in rows:
  assert x['signal']<x['entry']<x['exit'] and x['entry']-x['signal']==delay and x['entry']>prev
  assert x['capital']-capital<1e-8 and abs(x['capital']-capital)<1e-8
  assert isinstance(x['qty'],int) and x['qty']>0
  q=x['qty'];px=x['price'];xp=x['exit_price'];side=x['side'];t=x['ticker'];quote=at(t,x['entry']);eq=at(t,x['exit'])
  assert px==(quote.ask if side>0 else quote.bid) and xp==(eq.bid if side>0 else eq.ask)
  assert abs(x['margin']-q*px/lev)<1e-8 and x['margin']<=.5*capital+1e-8
  assert capital-x['margin']-x['entry_cost']>=20-1e-8
  assert x['estimated_stop_risk']<=min(2,.02*capital)+1e-8
  assert x['day_loss_before_entry']+x['estimated_stop_risk']<=6+1e-8
  assert x['margin']>=1.3*mrate(t,side)*q*px-1e-8
  fund=float(sum((-D(side)*D(q)*D(str(rate))*D(str(mark)) for ft,(rate,mark) in F[t].items() if x['entry']<ft<=x['exit']),D(0)))
  assert abs(x['funding']-fund)<1e-8
  fees=q*(px+xp)*.0012;friction=q*(px+xp)*slip/10000
  assert abs(x['net']-(side*q*(xp-px)+fund-fees-friction))<1e-8
  capital+=x['net'];prev=x['exit'];n+=1
out={'status':'PASS','scenarios':len(r),'ledger_records':n,'positive_scenarios':sum(v['net']>0 for v in r.values()),'warning':'Overlapping exploratory tests, not independent evidence. Stops use minute closes and later execution; gaps can exceed intended loss. Current margin proxy only.'}
(P/'audit_results.json').write_text(json.dumps(out,indent=2)+'\n');print(out)
