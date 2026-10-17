"""Exit-only paired counterfactual. No network, live client, or model fitting."""
from pathlib import Path
import gzip,json,math,datetime as dt
from decimal import Decimal as D,ROUND_CEILING,ROUND_FLOOR
from collections import defaultdict
from zoneinfo import ZoneInfo
P=Path(__file__).resolve().parent;ET=ZoneInfo('America/New_York')
SPLIT=dt.datetime.fromisoformat('2026-10-12T00:00:00-04:00').timestamp()
def threshold(r,c):
 if c['family']=='entry_drop':return r['entry_z']-c['level']
 if c['family']=='gate_drop':return r['entry_gate']-c['level']
 return c['level']
def grid():
 return [dict(id=f'{family}_{level:g}_wait{delay}_persist{persist}',family=family,level=level,delay=delay,persist=persist)
   for family,levels in [('entry_drop',[.25,.5,.75,1,1.5,2]),('gate_drop',[0,.25,.5,1]),('absolute',[0,.5,1,1.5])]
   for level in levels for delay in [0,60,120,180] for persist in [1,2]]
def tick_down(px,r):
 # Convert the sale's held-side bid to a valid YES tick, reducing proceeds.
 yes=px if r['side']==1 else 1-px
 for x in r['ranges']:
  lo,hi,step=map(D,(x['start'],x['end'],x['step']))
  if lo<=yes<=hi:
   yy=lo+((yes-lo)/step).to_integral_value(rounding=ROUND_FLOOR if r['side']==1 else ROUND_CEILING)*step
   return yy if r['side']==1 else 1-yy
 return None
def sale(q,p,mult):
 gross=q*p;trade=(D('.07')*D(str(mult))*q*p*(1-p)).quantize(D('.000001'),rounding=ROUND_CEILING)
 proceeds=(gross-trade).quantize(D('.0001'),rounding=ROUND_FLOOR)
 return proceeds,gross-proceeds
def simulate(r,c,slippage=.01,latency=0,frozen_sigma=False):
 out={k:r[k] for k in ['ticker','asset','lane','entered','close','settled','won','quantity','cost','entry_fee','entry_friction','hold_net','entry_z','entry_gate']}
 out.update(net=r['hold_net'],delta=0,exit=False,exit_fee=0,slippage_cost=0,trigger_ts=None,exit_ts=r['settled'],failed_quotes=0,too_late=0,missing_z=0)
 th=threshold(r,c);out['stop_z']=th;run=0
 for x in r['path']:
  if x['t']-r['entered']<c['delay']:continue
  z=x['z_frozen_sigma' if frozen_sigma else 'z']
  if z is None:run=0;out['missing_z']+=1;continue
  run=run+1 if z<th else 0
  if run<c['persist']:continue
  if out['trigger_ts'] is None:out['trigger_ts']=x['t']
  exit_t=x['t']+latency+1
  if exit_t>=r['close']:out['too_late']+=1;continue
  bids=[x['bid_close'],x['bid_next_open']] if latency==0 else [x['bid_delayed_open']]
  if any(v is None or not 0<v<=1 for v in bids):out['failed_quotes']+=1;continue
  quote=min(bids);px=tick_down(D(str(quote))-D(str(slippage)),r)
  if px is None or px<=0:out['failed_quotes']+=1;continue
  q=D(str(r['quantity']));proceeds,fee=sale(q,px,r['multiplier'])
  net=proceeds-D(str(r['cost']))-D(str(r['entry_fee']))-D(str(r['entry_friction']))
  out.update(net=float(net),delta=float(net)-r['hold_net'],exit=True,exit_fee=float(fee),
      slippage_cost=float(q*(D(str(quote))-px)),exit_ts=exit_t,exit_z=z,exit_price=float(px),
      trigger_ts=x['t'],quote_bid=quote)
  return out
 return out
def metrics(rows):
 losers=[r for r in rows if not r['won']];winners=[r for r in rows if r['won']];exits=[r for r in rows if r['exit']]
 day=defaultdict(lambda:dict(hold=0,net=0,delta=0,entries=0))
 for r in rows:
  d=dt.datetime.fromtimestamp(r['close'],ET).date().isoformat()
  for key,source in [('hold','hold_net'),('net','net'),('delta','delta')]:day[d][key]+=r[source]
  day[d]['entries']+=1
 eq=peak=dd=0
 for r in sorted(rows,key=lambda r:(r['exit_ts'],r['ticker'])):eq+=r['net'];peak=max(peak,eq);dd=max(dd,peak-eq)
 return dict(trades=len(rows),wins_if_held=len(winners),losses_if_held=len(losers),hold_net=sum(r['hold_net'] for r in rows),net=sum(r['net'] for r in rows),delta=sum(r['delta'] for r in rows),
   exits=len(exits),losers_exited=sum(r['exit'] for r in losers),losers_improved=sum(r['delta']>1e-8 for r in losers),loser_savings=sum(r['delta'] for r in losers),
   losses_fully_avoided=sum(r['net']>=0 for r in losers),winners_cut=sum(r['exit'] for r in winners),winners_turned_losses=sum(r['net']<0 for r in winners),
   winner_profit_sacrificed=-sum(r['delta'] for r in winners),exit_fees=sum(r['exit_fee'] for r in rows),slippage_cost=sum(r['slippage_cost'] for r in rows),
   remaining_net_losses=sum(r['net']<0 for r in rows),drawdown=dd,failed_quote_trades=sum(r['failed_quotes']>0 for r in rows),too_late_trades=sum(r['too_late']>0 for r in rows),daily=dict(day))
def main():
 cohorts=json.load(gzip.open(P/'paths.json.gz'));configs=grid()
 # Choose from early slice ONLY and persist the choice before later simulation.
 early=[r for r in cohorts['actual'] if r['close']<SPLIT]
 early_grid=[dict(config=c,metrics=metrics([simulate(r,c) for r in early])) for c in configs]
 ranked=sorted(early_grid,key=lambda x:(-round(x['metrics']['delta'],8),x['metrics']['winners_cut'],x['metrics']['exits'],x['config']['id']))
 selected=ranked[0]['config'];(P/'selection.json').write_text(json.dumps(dict(selection_period='Oct8–11 ET',selected=selected,early_grid=early_grid),indent=2)+'\n')
 results={};all_ledgers={}
 for name,rows in cohorts.items():
  results[name]={};all_ledgers[name]={}
  for c in configs:
   ledger=[simulate(r,c) for r in rows];all_ledgers[name][c['id']]=ledger
   results[name][c['id']]=dict(config=c,full=metrics(ledger),early=metrics([r for r in ledger if r['close']<SPLIT]),later=metrics([r for r in ledger if r['close']>=SPLIT]),
       assets={a:metrics([r for r in ledger if r['asset']==a]) for a in sorted({r['asset'] for r in ledger})})
 sensitivity={}
 for name,rows in cohorts.items():
  sensitivity[name]={}
  for label,slip,lag,frozen in [('exit_1c',.01,0,False),('exit_2c',.02,0,False),('exit_5c',.05,0,False),('delay60_exit1c',.01,60,False),('frozen_sigma_exit1c',.01,0,True)]:
   ledger=[simulate(r,selected,slip,lag,frozen) for r in rows];sensitivity[name][label]=dict(metrics=metrics(ledger),ledger=ledger)
 (P/'results.json').write_text(json.dumps(results,indent=2)+'\n')
 (P/'sensitivity.json').write_text(json.dumps(sensitivity,indent=2)+'\n')
 with gzip.open(P/'all_ledgers.json.gz','wt') as f:json.dump(all_ledgers,f)
 print('SELECTED',selected)
 for name in cohorts:
  print(name,'selected',json.dumps(results[name][selected['id']]['full']))
  print(name,'early/later deltas',results[name][selected['id']]['early']['delta'],results[name][selected['id']]['later']['delta'])
  for r in sorted(results[name].values(),key=lambda x:x['full']['delta'],reverse=True)[:5]:print('HINDSIGHT',name,r['config']['id'],r['full']['net'],r['full']['losers_exited'],r['full']['winners_cut'])
if __name__=='__main__':main()
