import gzip,json,pickle,math
from pathlib import Path
from collections import defaultdict
import study as s
P=s.P;markets=pickle.loads((P/'features.pickle').read_bytes());pred=pickle.loads((P/'predictions.pickle').read_bytes());lookup={m['ticker']:m for m in markets};bars=pickle.loads((P/'bars.pickle').read_bytes())
# Load the defined partial-book adapter without rerunning its report loop.
namespace={};exec(compile((P/'book_overlay.py').read_text().split("frozen=json.loads")[0],str(P/'book_overlay.py'),'exec'),namespace)
MODES=[('hold',None,None),('stop10',.10,None),('stop15',.15,None),('stop20',.20,None),('tp97',None,.97),('tp98',None,.98),('tp97stop10',.10,.97),('tp97stop15',.15,.97),('cross_strike',None,None)]
frozen=json.loads((P/'choices_frozen.json').read_text())['choices'];out={};logs={}
def apply(pool,mode,stop,tp,stress):
 rr=[]
 for source in pool:
  r=dict(source);r['exit_kind']='settlement';r['entry_won']=r['won'];m=lookup[r['ticker']]
  if mode=='hold':rr.append(r);continue
  # Predetermined reserve covers entry debit and both friction charges, not a future observed exit.
  r['reserve']=max(r['reserve'],5*r['entry']+r['fee']+2*stress*5)
  for U in range(r['t']+60,m['close'],60):
   pair=s.quote(m['candles'].get(U));nxt=s.quote(m['candles'].get(U+60),'o')
   if pair is None or nxt is None:continue
   bid=pair[0] if r['side']==1 else 1-pair[1];nextbid=nxt[0] if r['side']==1 else 1-nxt[1]
   val=bars[m['asset']].get(U-60);ref=bars[m['asset']].get(m['start']-60)
   crossed=val is not None and ref is not None and r['side']*(val/ref-1)<0
   why='stop' if stop is not None and bid<=r['entry']-stop else 'takeprofit' if tp is not None and bid>=tp else 'cross_strike' if mode=='cross_strike' and crossed else None
   if not why or U+3>=m['close']:continue
   price=min(bid,nextbid)
   if not 0<price<1:continue
   paid=s.fee(price,5,m['multiplier']);r.update(payout=price-paid/5-stress,settled=U+3,exit_kind=why,exit_price=price,exit_fee=paid,exit_friction=stress*5,net_before_friction=5*(price-r['entry'])-r['fee']-paid-stress*5)
   # Additional exit friction is in net_before_friction; portfolio adds entry friction.
   r['won']=int(r['net_before_friction']-stress*5>0);break
  rr.append(r)
 return rr

# Evaluate development/selection only before selecting an exit.
cache={}
for a in s.ASSETS:
 ids=sorted({0,frozen[a]['selected']});out[a]={};logs[a]={}
 for cid in ids:
  pool,_=namespace['supplemented'](s.CONFIG[cid],'observed');pool=[r for r in pool if r['asset']==a];cache[(a,cid)]=pool;out[a][str(cid)]={};logs[a][str(cid)]={}
  for name,stop,tp in MODES:
   out[a][str(cid)][name]={};logs[a][str(cid)][name]={}
   for label,stress in (('base',.01),('cost2',.02),('cost3',.03)):
    rows=apply(pool,name,stop,tp,stress);out[a][str(cid)][name][label]={};logs[a][str(cid)][name][label]={}
    for period in ('development','selection'):
     metric,ledger=s.evaluate(rows,a,period,stress);assert abs(metric['final_cash']-100-metric['net'])<1e-5
     metric['exit_counts']=dict(__import__('collections').Counter(r['exit_kind'] for r in ledger));out[a][str(cid)][name][label][period]=metric;logs[a][str(cid)][name][label][period]=ledger
choices={}
for a,rules in out.items():
 choices[a]={}
 for cid,modes in rules.items():
  good=[]
  for name,sc in modes.items():
   ok=sc['base']['development']['trades']>=25 and sc['base']['selection']['trades']>=15 and all(sc[x][p]['net']>0 for x in ('cost2','cost3') for p in ('development','selection'))
   if ok:
    score=min((sc[x][p]['net']-.1*sc[x][p]['drawdown'])/((s.PERIODS[p][1]-s.PERIODS[p][0])/86400) for x in ('cost2','cost3') for p in ('development','selection'));good.append((score,name))
  choices[a][cid]=sorted(good,reverse=True)[0][1] if good else None
s.save('exit_choices_frozen.json',dict(choices=choices,chronological_not_pristine=True,protocol='EXIT_PROTOCOL.txt',later_exit_results_not_calculated=True))
for (a,cid),pool in cache.items():
 for name,stop,tp in MODES:
  for label,stress in (('base',.01),('cost2',.02),('cost3',.03)):
   rows=apply(pool,name,stop,tp,stress)
   for period in ('confirmation','recent','pooled'):
    metric,ledger=s.evaluate(rows,a,period,stress);assert abs(metric['final_cash']-100-metric['net'])<1e-5
    metric['exit_counts']=dict(__import__('collections').Counter(r['exit_kind'] for r in ledger));out[a][str(cid)][name][label][period]=metric;logs[a][str(cid)][name][label][period]=ledger
s.save('exit_results.json',out)
with gzip.open(P/'exit_ledgers.json.gz','wt') as f:json.dump(logs,f)
print('FROZEN EXIT CHOICES',choices)
for a,cc in choices.items():
 for cid,name in cc.items():
  if name is None:continue
  print(a,cid,name,{p:{k:out[a][cid][name]['base'][p][k] for k in ('trades','wins','losses','net','drawdown')} for p in s.PERIODS},flush=True)
