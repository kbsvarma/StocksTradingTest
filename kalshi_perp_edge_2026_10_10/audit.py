"""Independent checks on raw funding, monetary attribution and causal timestamps."""
from core import *
from decimal import Decimal as Dec
from collections import defaultdict
rawf={t:json.load(open(OLD/(t+'_funding.json'))) for t in Q};checked=0
def funding(t,start,end,qty,side):
 return float(sum((-Dec(side)*Dec(qty)*Dec(r['funding_rate'])*Dec(r['mark_price']) for r in rawf[t] if start<T(r['funding_time'])<=end),Dec(0)))
for mode in ['pair','trend']:
 results=json.load(open(P/(mode+'_results.json')));ls=json.load(gzip.open(P/(mode+'_ledgers.json.gz'),'rt'))
 for key,rows in ls.items():
  for r in rows:
   assert r['entry']<r['exit']
   assert r['meta']['signal']<r['entry']
   fund=0
   for l in r['legs']:
    assert isinstance(l['qty'],int) and l['qty']>0
    f=funding(l['ticker'],r['entry'],r['exit'],l['qty'],l['side']);assert abs(f-l['fund'])<1e-6;fund+=f
   assert abs(fund-r['funding'])<1e-6
   assert abs(r['net']-(r['gross']+fund-r['fees']-r['slippage']))<1e-6
   checked+=1
  assert abs(sum(r['net'] for r in rows)-results[key]['net'])<1e-5
etfs=json.load(open(P/'etf_results.json'));restarts=json.load(open(P/'etf_restarts.json'));epaths=json.load(gzip.open(P/'etf_paths.json.gz','rt'));details={}
for r in etfs+restarts:
 fund=funding('KXBTCPERP',r['start'],r['perp_exit'],r['perp_contracts'],-1)
 assert abs(fund-r['funding'])<1e-6
 assert abs(r['net']-(r['gross_perp']+r['gross_etf']+fund-r['fees']-r['slippage']))<1e-6
 assert r['idle_cash']>=0
 assert r['initial_perp_margin']>=1.3*mrate('KXBTCPERP',-1)*r['perp_contracts']*r['entry_perp']
 checked+=1
 if r['key']in epaths:
  weekly=defaultdict(float);prior=10000.
  for p in epaths[r['key']]:
   d=dt.datetime.fromtimestamp(p['t'],UTC).date();wk=(d-dt.timedelta(days=d.weekday())).isoformat();weekly[wk]+=p['equity']-prior;prior=p['equity']
  details[r['key']]={'weekly':dict(weekly),'positive_weeks':sum(v>0 for v in weekly.values()),'weeks':len(weekly),'half_funding_fixed_path_net':r['net']-fund*.5,'zero_funding_fixed_path_net':r['net']-fund}
coverage={}
for t,q in Q.items():
 times=np.arange(START,END+60,60);ok=np.array([at(t,int(s))is not None for s in times]);runs=[];n=0
 for b in ok:
  if not b:n+=1
  elif n:runs.append(n);n=0
 if n:runs.append(n)
 coverage[t]={'calendar_minutes':len(times),'usable_minutes':int(ok.sum()),'unusable_or_absent':int((~ok).sum()),'longest_gap_minutes':max(runs,default=0),'funding_events':sum(START<T(r['funding_time'])<=END for r in rawf[t])}
# The equal-weight ensemble allocates half of total capital to each independently.
pp=json.load(gzip.open(P/'trend_paths.json.gz','rt'));joint={}
for rule in ['ensemble','buyhold']:
 for lev in [1,1.5,2,3,'vol30']:
  a={r['t']:r['equity'] for r in pp[f'KXBTCPERP/{rule}/L{lev}/slip1']};b={r['t']:r['equity'] for r in pp[f'KXETHPERP/{rule}/L{lev}/slip1']};path=[{'t':s,'equity':(a[s]+b[s])/2} for s in sorted(a.keys()&b.keys())];m=metrics(path,[]);joint[f'{rule}/L{lev}']={k:m[k] for k in ['net','return_pct','drawdown_pct']}
save('audit_results.json',{'status':'PASS','ledger_and_etf_checks':checked,'coverage':coverage,'etf_robustness':details,'joint_trend':joint,'note':'Funding stress holds the realized trade path fixed; it is attribution, not a resimulation of margin exits. Margin uses present parameters and quotes, not historical liquidation marks.'})
print('PASS',checked,'ledger/ETF records');print(coverage);print('joint',joint['ensemble/L1'])
