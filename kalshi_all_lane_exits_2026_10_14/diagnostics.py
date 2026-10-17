from portfolio import *
from fast_entry_screen import quickpath
def bootstrap(rows,key='delta'):
 by=collections.defaultdict(float)
 for r in rows:by[day(r.get('entered',r['realized']))]+=r[key]
 dates=sorted(by);values=np.array([by[d] for d in dates]);rng=np.random.default_rng(20261014)
 if not len(values):return None
 samples=np.empty(10000)
 for i in range(len(samples)):
  starts=rng.integers(0,len(values),size=(len(values)+1)//2);ix=np.ravel(np.column_stack((starts,(starts+1)%len(values))))[:len(values)];samples[i]=values[ix].sum()
 return dict(days=len(values),total=float(values.sum()),interval95=np.quantile(samples,[.025,.975]).tolist(),method='2-day circular moving-block bootstrap; exploratory, not adjusted for parameter search',daily=dict(by))
def main():
 d=pickle.load(gzip.open(P/'inputs.pkl.gz'));ss=pickle.load(gzip.open(P/'seconds.pkl.gz'));rr=d['actual'];cc={c['id']:c for c in rules()};out={};led=json.load(gzip.open(P/'actual_minute_ledgers.json.gz'))
 print('minute ledger keys',list(led)[:2],flush=True)
 chosen=['drop_1_g0_p0','drop_2_g0_p0','drop_2_g120_p60','price_0.2_g0_p0']
 for k in chosen:
  xs=led[k];out[k]=dict(uncertainty=bootstrap([dict(x,entered=r['entered']) for r,x in zip(rr,xs)]),bylane={lane:metrics([x for x in xs if x['lane']==lane]) for lane in [Z2,Z3]},bytime={f'{lo}-{hi}':metrics([x for r,x in zip(rr,xs) if lo<=(r['close']-r['entered'])/60<hi]) for lo,hi in [(0,3),(3,5),(5,10),(10,15)]})
  # A five-contract normalization is synthetic; scale actual fee percontract.
  normalized=[]
  for r in rr:
   q=5/r['quantity'];n=dict(r,quantity=5,cost=r['cost']*q,entry_fee=r['entry_fee']*q,hold_net=r['hold_net']*q);normalized.append(exit_result(n,cc[k],path(n,d)))
  out[k]['normalized5']=metrics(normalized)
 fast=json.load(gzip.open(P/'actual_fast_details.json.gz'))
 for k in ['i1_drop_2_g0_p0','i10_drop_2_g0_p0','i30_drop_2_g0_p0','i60_drop_2_g0_p0','i1_drop_2_g120_p60']:
  out[k]={mode:dict(uncertainty=bootstrap([dict(x,entered=r['entered']) for r,x in zip(rr,fast[k][mode])]),bylane={lane:metrics([x for x in fast[k][mode] if x['lane']==lane]) for lane in [Z2,Z3]}) for mode in ['low','high']}
 # Signals sensitivity; quote envelopes remain unchanged. Rolling sigma isolates
 # source-frequency effects from the separate frozen-versus-rolling-vol choice.
 sensitivities=[]
 for interval in [1,10,30,60]:
  for model in ['frozen','rolling','direct_cf','delay3','delay10','minute_control']:
   pp=[]
   for r in rr:
    p=quickpath(r,d,ss,interval);tt=p['t'];a=r['asset'];start,values=ss[a]
    if model in ['delay3','delay10','direct_cf']:
     delay=3 if model=='delay3' else 10 if model=='delay10' else 1;spot=values[tt-delay-start]
     if a in CRYPTO and model!='direct_cf':spot=r['entry_spot']*spot/values[int(r['t'])-1-start]
     p['z']=r['side']*(spot/r['reference']-1)/(r['entry_sigma']*np.sqrt((r['close']-tt)/60))
    elif model in ['rolling','minute_control']:
     sig=np.array([d['stats'][a].get(int(t)//60*60,(np.nan,np.nan))[1] for t in tt])
     if model=='rolling':p['z']=p['z']*r['entry_sigma']/sig
     else:
      spot=np.array([d['stats'][a].get(int(t)//60*60,(np.nan,np.nan))[0] for t in tt]);p['z']=r['side']*(spot/r['reference']-1)/(sig*np.sqrt((r['close']-tt//60*60)/60))
    pp.append(p)
   for k in ['drop_2_g0_p0','drop_2_g120_p60','drop_1_g0_p0']:
    low=[exit_result(r,cc[k],p,'low') for r,p in zip(rr,pp)];high=[exit_result(r,cc[k],p,'high') for r,p in zip(rr,pp)];sensitivities.append(dict(interval=interval,model=model,exit=k,low=metrics(low),high=metrics(high)))
  print('sensitivity',interval,flush=True)
 save('diagnostics.json',out);save('signal_sensitivity.json',sensitivities)
 portfolios=json.load(gzip.open(P/'portfolio_later_ledgers.json.gz'));base=portfolios['active7/current/hold'];dailybase=collections.defaultdict(float)
 for r in base:dailybase[day(r['entered'])]+=r['net']
 comparisons={}
 for name,rows in portfolios.items():
  daily=collections.defaultdict(float)
  for r in rows:daily[day(r['entered'])]+=r['net']
  pair=[dict(entered=datetime.datetime.fromisoformat(k+'T12:00:00-04:00').timestamp(),realized=0,delta=daily[k]-dailybase[k]) for k in sorted(set(daily)|set(dailybase))];comparisons[name]=bootstrap(pair)
 save('portfolio_uncertainty.json',comparisons)
if __name__=='__main__':main()
