"""Independent selected-rule replay, combinations and parameter sensitivity."""
from pathlib import Path
from decimal import Decimal as D,ROUND_CEILING,ROUND_FLOOR
import sys,json,gzip,pickle,math,hashlib,subprocess,datetime
import numpy as np
P=Path(__file__).resolve().parent;R=P.parent
sys.path.insert(0,str(R/'kalshi_timed_z_exits_2026_10_14'))
import grid as g
import details as dt
from search import save
def scalar(r,c,p):
 since=None;peak=r['entry_z']*math.sqrt((r['close']-r['t'])/60);origin=peak
 for j,(t,z) in enumerate(zip(p['t'],p['z'])):
  rem=r['close']-t;age=t-r['entered'];dist=z*math.sqrt(rem/60);peak=max(peak,dist)
  f=c['family'];lev=c['level'];bad=z<lev
  if f=='hold':bad=False
  elif f=='entry_distance':bad=dist<lev*origin
  elif f=='peak_distance':bad=dist<lev*peak
  elif f=='velocity':bad=bad and j>=c['lookback'] and p['z'][j-c['lookback']]-z>=c['drop']
  bad=bad and age>=c['grace'] and c['lo']<rem<=c['hi']
  if not bad:since=None;continue
  if since is None:since=t
  if t-since>=c['persist']:return j
 return -1
def record(r,c,p,limit=0,extra=0):
 j=scalar(r,c,p);x={k:r[k] for k in ['ticker','asset','lane','won','entered','close','hold_net','quantity','cost','entry_fee']}
 x.update(config=c,sell_limit=limit,trigger=int(p['t'][j]) if j>=0 else None,trigger_z=float(p['z'][j]) if j>=0 else None,exit=False,net=r['hold_net'],fee=0.,friction=0.,price=None)
 if j>=0 and p['low']['fill'][j] and p['low']['price'][j]>=limit:
  x.update(exit=True,net=float(p['low']['net'][j])-extra*r['quantity'],fee=float(p['low']['fee'][j]),friction=float(p['low']['friction'][j])+extra*r['quantity'],price=float(p['low']['price'][j]))
 x.update(delta=x['net']-r['hold_net'],fees_only_net=x['net']+x['friction']);return x
def summarize(xs):
 return dict(periods=dt.split_metrics(xs),assets={a:dt.metrics([x for x in xs if x['asset']==a]) for a in g.ASSETS},days={day:dt.metrics([x for x in xs if datetime.datetime.fromtimestamp(x['close'],datetime.timezone(datetime.timedelta(hours=-4))).date().isoformat()==day]) for day in ['2026-10-12','2026-10-13','2026-10-14']})
def main():
 d=pickle.load(gzip.open(g.PREV/'inputs.pkl.gz','rb'));ss=pickle.load(gzip.open(g.PREV/'seconds.pkl.gz','rb'));rr=d['actual'];cc=json.loads((P/'configs.json').read_text());by={c['id']:c for c in cc};ar=dict(np.load(P/'arrays.npz'));old=json.loads((g.P/'selection.json').read_text())['asset_specific']['0']['choices']
 paths={r['ticker']:g.prepare(r,d,ss) for r in rr};paths3={r['ticker']:g.prepare(r,d,ss,3) for r in rr}
 btc=by['velocity_z2_d1_l60_p0_r0-900'];wti=by['absolute_1.75_g0_p60_r0-300'];shock=by['velocity_z0.5_d1_l5_p0_r30-900'];hold=by['hold']
 configs={'prior_map':{},'WTI_only_addition':{'WTI':(wti,.9)},'BTC_WTI_replacements':{'BTC':(btc,.8),'WTI':(wti,.9)},'BTC_WTI_additions':{'BTC':(btc,.8),'WTI':(wti,.9)},'WTI_shock_addition':{'WTI':(shock,0)},'NATGAS_z225_diagnostic':{'NATGAS':(by['absolute_2.25_g0_p30_r0-900'],0)}}
 nat=dict(id='NATGAS_z5_last120_limit98',family='absolute',level=5,grace=0,persist=0,lo=0,hi=120)
 configs['NATGAS_harvest_addition']={'NATGAS':(nat,.98)}
 configs['WTI_NATGAS_additions']={'WTI':(wti,.9),'NATGAS':(nat,.98)}
 configs['BTC_WTI_NATGAS_additions']={'BTC':(btc,.8),'WTI':(wti,.9),'NATGAS':(nat,.98)}
 results={};ledgers={}
 def run(name,mapping,pp,extra=0):
  xs=[]
  for r in rr:
   p=pp[r['ticker']];base=dt.record(r,old[r['asset']]['config'],p,extra_friction=extra);base['sell_limit']=0
   if r['asset'] not in mapping:x=base
   else:
    c,limit=mapping[r['asset']];x=record(r,c,p,limit,extra)
    if name!='BTC_WTI_replacements':
     candidates=[v for v in [base,x] if v['exit']]
     if candidates:x=min(candidates,key=lambda v:v['trigger'])
     else:x=base
   xs.append(x)
  return xs
 for name,mapping in configs.items():
  xs=run(name,mapping,paths);ledgers[name]=xs;results[name]=summarize(xs)
  if name in ['WTI_only_addition','BTC_WTI_additions','NATGAS_harvest_addition','WTI_NATGAS_additions','BTC_WTI_NATGAS_additions']:
   results[name]['sensitivity']={'latency3s':summarize(run(name,mapping,paths3)),'friction2c':summarize(run(name,mapping,paths,.01))}
 # Nearby settings changed one at a time; do not select a new best from this check.
 near={}
 for asset,c0,limit0 in [('BTC',btc,.8),('WTI',wti,.9),('NATGAS',nat,.98)]:
  variants=[('base',dict(c0),limit0)]
  for delta in [-.25,.25]:variants.append((f'z{delta:+g}',dict(c0,level=c0['level']+delta),limit0))
  for delta in [-.05,.05]:variants.append((f'limit{delta:+g}',dict(c0),round(limit0+delta,2)))
  if asset=='BTC':
   for delta in [-.25,.25]:variants.append((f'drop{delta:+g}',dict(c0,drop=c0['drop']+delta),limit0))
   for val in [30,90]:variants.append((f'lookback{val}',dict(c0,lookback=val),limit0))
  elif asset=='WTI':
   for val in [30,45,75,90]:variants.append((f'confirm{val}',dict(c0,persist=val),limit0))
  if asset=='NATGAS':
   variants=[('base',dict(c0),limit0)]+[(f'z{lev}',dict(c0,level=lev),limit0) for lev in [4,6,8]]+[(f'rem{rem}',dict(c0,hi=rem),limit0) for rem in [180,90,65,60]]+[(f'limit{lim}',dict(c0),lim) for lim in [.97,.99]]
  for label,c,lim in variants:
   xs=[record(r,c,paths[r['ticker']],lim) for r in rr if r['asset']==asset];near[asset+'_'+label]=dict(config=c,limit=lim,summary=summarize(xs))
 results['nearby']=near
 # Original first-trigger arrays and all cash arithmetic independently verified.
 rng=np.random.default_rng(20261015);checks=0;fees=0
 for _ in range(2000):
  k=int(rng.integers(len(cc)));j=int(rng.integers(len(rr)));r=rr[j];x=record(r,cc[k],paths[r['ticker']]);assert abs(x['net']-ar['net'][k,j])<1e-9;assert (x['trigger'] if x['trigger'] is not None else -1)==ar['trigger'][k,j];checks+=1
 for xs in ledgers.values():
  for x in xs:
   assert abs(x['net']-x['hold_net']-x['delta'])<1e-9
   if not x['exit']:continue
   q,p=D(str(x['quantity'])),D(str(x['price']));fee=(D('.07')*q*p*(1-p)).quantize(D('.000001'),rounding=ROUND_CEILING);cash=(q*p-fee).quantize(D('.0001'),rounding=ROUND_FLOOR);net=cash-D(str(x['cost']))-D(str(x['entry_fee']))-D(str(x['friction']))
   assert abs(float(net)-x['net'])<1e-9;assert x['price']+1e-9>=x['sell_limit'];assert x['trigger']+1<x['close'];fees+=1
 for name in configs:
  for m in results[name]['periods'].values():assert abs(m['hold']+m['loss_savings']-m['winner_cost']-m['net'])<1e-9
 save('selected_results.json',results)
 with gzip.open(P/'selected_ledgers.json.gz','wt') as f:json.dump(ledgers,f)
 base=json.loads((g.P/'preservation.json').read_text());live=R/'kalshi_revalidation_2026_09_12/live_v1';code={n:hashlib.sha256((live/n).read_bytes()).hexdigest() for n in base['hashes']};flags={n:hashlib.sha256((live/'state'/n).read_bytes()).hexdigest() for n in base['flags']};services={n:subprocess.check_output(['systemctl','--user','show',n,'-p','ActiveState','-p','MainPID','-p','NRestarts'],text=True) for n in base['services']}
 assert code==base['hashes'] and flags==base['flags'] and services==base['services']
 save('audit.json',dict(scalar_random_matches=checks,independent_exit_cash_checks=fees,cohort_trades=len(rr),live_code_unchanged=True,live_flags_unchanged=True,live_services_pids_unchanged=True,no_orders=True,uses_latest_losses_for_selection=True,first_signal_sell_limit_model=True))
 for name in configs:print(name,json.dumps(results[name]['periods']),flush=True)
 print('NEARBY')
 for n,x in near.items():m=x['summary']['periods'];print(n,'full_delta',round(m['full']['delta'],4),'late_delta',round(m['later']['delta'],4),'cuts',m['full']['winners_cut'],flush=True)
if __name__=='__main__':main()
