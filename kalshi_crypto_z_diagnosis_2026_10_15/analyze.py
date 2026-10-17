"""Independent population-volatility calculation and matched-hours decomposition."""
import collections,datetime,json,math,statistics,time
from pathlib import Path
from zoneinfo import ZoneInfo
P=Path(__file__).resolve().parent;D=P/'receipts';ET=ZoneInfo('America/New_York')
captured=json.loads((P/'captured.json').read_text());export=json.loads((P/'export.json').read_text())
def ts(day,h,m):return int(datetime.datetime(2026,10,day,h,m,tzinfo=ET).timestamp())
def quant(xs):
 xs=sorted(xs)
 return dict(n=len(xs),mean=statistics.mean(xs),median=statistics.median(xs),p10=xs[int(.1*(len(xs)-1))],p90=xs[int(.9*(len(xs)-1))],max=xs[-1],min=xs[0]) if xs else dict(n=0)
def corr(x,y):return statistics.correlation(x,y) if len(x)>1 and len(set(x))>1 and len(set(y))>1 else None
bars={};issues=[];duplicates=[];http=[]
for path in sorted(D.glob('coinbase_*.json')):
 if '.failure' in path.name:continue
 _,asset,day,k=path.stem.split('_');receipt=json.loads(path.read_text());http.append({k:receipt.get(k) for k in ('url','status','requested','received','headers')})
 data=receipt['data'];assert isinstance(data,list)
 for row in data:
  t,low,high,op,cl,volume=row;t=int(t)
  if t%60 or not (0<float(low)<=float(op)<=float(high) and float(low)<=float(cl)<=float(high)) or float(volume)<0:issues.append([asset,t,row])
  key=(asset,t)
  if key in bars and bars[key]!=row:duplicates.append([asset,t,bars[key],row])
  bars[key]=row
def feature(asset,T,provider=None):
 source=bars if provider is None else provider
 close=(T//900+1)*900;opened=close-900;rem=(close-T)/60
 keys=list(range(T-3660,T,60))
 if any((asset,k) not in source for k in keys) or (asset,opened-60) not in source:return None
 vals=[float(source[asset,k][4]) for k in keys];ref=float(source[asset,opened-60][4]);spot=vals[-1]
 ret=[math.log(b/a) for a,b in zip(vals,vals[1:])];mean=sum(ret)/60
 # Independent two-pass population variance, not the production pstdev call.
 ss=[(x-mean)**2 for x in ret];sigma=math.sqrt(math.fsum(ss)/60)
 if not sigma:return None
 move=spot/ref-1;z=move/(sigma*math.sqrt(rem))
 within=[math.log(float(source[asset,k][4])/float(source[asset,k-60][4])) for k in range(opened,T,60)]
 idx=max(range(len(ss)),key=ss.__getitem__)
 return dict(asset=asset,T=T,day=datetime.datetime.fromtimestamp(T,ET).day,remaining=rem,spot=spot,reference=ref,
  move_bp=move*10000,abs_move_bp=abs(move)*10000,sigma_bp=sigma*10000,denominator_bp=sigma*math.sqrt(rem)*10000,z=z,abs_z=abs(z),
  efficiency=abs(math.log(spot/ref))/sum(abs(x) for x in within) if sum(abs(x) for x in within)>0 else 0,
  variance_max_share=max(ss)/sum(ss),largest_return_ts=keys[idx+1],largest_return_bp=ret[idx]*10000,
  sigma_pstdev_difference=sigma-statistics.pstdev(ret),zero_returns=sum(x==0 for x in ret),
  sigma_15_bp=statistics.pstdev(ret[-15:])*10000,sigma_30_bp=statistics.pstdev(ret[-30:])*10000,
  sigma_without_largest_bp=statistics.pstdev(ret[:idx]+ret[idx+1:])*10000)
features=[];missing=[]
for day in range(3,16):
 for asset in ('BTC','ETH','SOL','XRP'):
  for T in range(ts(day,10,55),ts(day,15,53),60):
   rem=((T//900+1)*900-T)/60
   if not 3<=rem<=10:continue
   f=feature(asset,T)
   if f is None:missing.append([day,asset,T])
   else:features.append(f)
index={(r['asset'],r['T']):r for r in features};rechecks=[]
for r in captured:
 f=index.get((r['asset'],r['decision_ts']))
 if f is None or r.get('z') is None:continue
 sign=1 if r['side']=='yes' else -1;z=sign*f['z'];required=r.get('required_z',3 if r['lane']=='premium_z3' else 2)
 rechecks.append(dict(asset=r['asset'],day=r['day'],lane=r['lane'],T=r['decision_ts'],ticker=r['ticker'],side=r['side'],ask=r['signal_ask'],
  captured_z=r['z'],recomputed_z=z,delta=z-r['z'],required=required,changed_pass=(z>=required)!=(r['z']>=required),
  captured_spot=r['spot'],spot=f['spot'],spot_diff_bp=(f['spot']/r['spot']-1)*10000,
  captured_reference=r['strike_proxy'],reference=f['reference'],reference_diff_bp=(f['reference']/r['strike_proxy']-1)*10000,
  captured_sigma_bp=r['sigma']*10000,sigma_bp=f['sigma_bp'],sigma_ratio=f['sigma_bp']/(r['sigma']*10000),
  captured_move_bp=sign*(r['spot']/r['strike_proxy']-1)*10000,move_bp=sign*f['move_bp'],remaining=f['remaining']))
def summary(rs):
 return dict(n=len(rs),**{k:quant([r[k] for r in rs]) for k in ('abs_z','abs_move_bp','sigma_bp','denominator_bp','efficiency','variance_max_share','zero_returns','sigma_15_bp','sigma_30_bp')},
  z2=sum(r['abs_z']>=2 for r in rs),z3=sum(r['abs_z']>=3 for r in rs))
daily=[];recorded=[]
for day in range(3,16):
 for asset in ('BTC','ETH','SOL','XRP'):
  rs=[r for r in features if r['day']==day and r['asset']==asset]
  daily.append(dict(day=day,asset=asset,**summary(rs)))
  for lane in ('cushion_z2','premium_z3'):
   rr=[r for r in rechecks if r['day']==day and r['asset']==asset and r['lane']==lane]
   recorded.append(dict(day=day,asset=asset,lane=lane,n=len(rr),z=quant([r['captured_z'] for r in rr]),
     sigma_bp=quant([r['captured_sigma_bp'] for r in rr]),move_bp=quant([r['captured_move_bp'] for r in rr]),
     original_pass=sum(r['captured_z']>=r['required'] for r in rr),current_threshold_pass=sum(r['captured_z']>=(3 if asset=='BTC' or lane=='premium_z3' else 2) for r in rr),
     remaining=quant([r['remaining'] for r in rr])))
comparisons={}
for asset in ('BTC','ETH','SOL','XRP'):
 today=[r for r in features if r['day']==15 and r['asset']==asset]
 prior=[r for r in features if 8<=r['day']<=14 and r['asset']==asset]
 # Pair each exact clock minute. Take its prior-day median denominator and move.
 # These are diagnostics, not a tradable alternate strategy or a fill backtest.
 paired=[]
 for r in today:
  hms=r['T']-ts(15,0,0)
  old=[x for x in prior if x['T']-ts(x['day'],0,0)==hms]
  prior_sigma=statistics.median(x['sigma_bp'] for x in old);prior_move=statistics.median(x['abs_move_bp'] for x in old)
  paired.append(dict(T=r['T'],remaining=r['remaining'],z=r['abs_z'],move_ratio=r['abs_move_bp']/prior_move if prior_move else None,
    sigma_ratio=r['sigma_bp']/prior_sigma,prior_sigma_z=r['abs_move_bp']/(prior_sigma*math.sqrt(r['remaining'])),
    prior_move_z=prior_move/r['denominator_bp'],prior_median_z=statistics.median(x['abs_z'] for x in old)))
 comparisons[asset]=dict(today=summary(today),prior=summary(prior),paired_stats={k:quant([r[k] for r in paired if r[k] is not None]) for k in ('move_ratio','sigma_ratio','z','prior_sigma_z','prior_move_z','prior_median_z')},
  matched_minute_counterfactual_z2=sum(r['prior_sigma_z']>=2 for r in paired),paired=paired)
kraken={};cross=[];kraken_errors=[]
for asset in ('BTC','ETH','SOL','XRP'):
 path=D/('kraken_'+asset+'.json')
 if not path.exists():continue
 payload=json.loads(path.read_text())['data']
 if payload.get('error'):kraken_errors.append([asset,payload['error']]);continue
 data=next(v for k,v in payload['result'].items() if k!='last')
 for row in data:
  t,op,hi,lo,cl,vwap,volume,count=row
  if int(t)<int(time.time()//60)*60:kraken[asset,int(t)]=[int(t),float(lo),float(hi),float(op),float(cl),float(volume)]
 for f in [x for x in features if x['day']==15 and x['asset']==asset]:
  k=feature(asset,f['T'],kraken)
  if k:cross.append(dict(asset=asset,T=f['T'],coinbase_spot=f['spot'],kraken_spot=k['spot'],price_difference_bp=(f['spot']/k['spot']-1)*10000,
    coinbase_sigma_bp=f['sigma_bp'],kraken_sigma_bp=k['sigma_bp'],sigma_ratio=f['sigma_bp']/k['sigma_bp'],
    coinbase_z=f['z'],kraken_z=k['z'],z_difference=f['z']-k['z']))
cross_summary={a:dict(n=len(rs),price_difference_bp=quant([abs(r['price_difference_bp']) for r in rs]),sigma_ratio=quant([r['sigma_ratio'] for r in rs]),
 z_difference=quant([abs(r['z_difference']) for r in rs]),spot_correlation=corr([r['coinbase_spot'] for r in rs],[r['kraken_spot'] for r in rs]),
 z_correlation=corr([r['coinbase_z'] for r in rs],[r['kraken_z'] for r in rs])) for a in ('BTC','ETH','SOL','XRP') if (rs:=[r for r in cross if r['asset']==a])}
recheck_summary={label:dict(n=len(rs),z_delta=quant([abs(r['delta']) for r in rs]),spot_delta_bp=quant([abs(r['spot_diff_bp']) for r in rs]),
 reference_delta_bp=quant([abs(r['reference_diff_bp']) for r in rs]),sigma_ratio=quant([r['sigma_ratio'] for r in rs]),flips=[r for r in rs if r['changed_pass']])
 for label in ('all','today') if (rs:=[r for r in rechecks if label=='all' or r['day']==15])}
out=dict(captured=time.time(),feature_count=len(features),missing=missing,invalid_bars=issues,overlap_revisions=duplicates,
 variance_implementation_max_difference=max(abs(r['sigma_pstdev_difference']) for r in features),daily=daily,recorded=recorded,
 comparisons=comparisons,feed_rechecks=recheck_summary,independent_kraken=cross_summary,kraken_errors=kraken_errors)
for name,data in [('analysis.json',out),('features.json',features),('feed_rechecks.json',rechecks),('cross_venue.json',cross),('http_summary.json',http)]:
 (P/name).write_text(json.dumps(data,indent=2)+'\n')
print(json.dumps(dict(features=len(features),missing=len(missing),invalid_bars=len(issues),overlap_revisions=len(duplicates),
  recheck={k:dict(n=v['n'],z_delta=v['z_delta'],flips=len(v['flips'])) for k,v in recheck_summary.items()},
  comparisons={a:{label:{k:summary[k]['median'] if isinstance(summary[k],dict) else summary[k] for k in ('n','abs_z','abs_move_bp','sigma_bp','efficiency','z2','z3')} for label,summary in v.items() if label in ('today','prior')} for a,v in comparisons.items()},kraken=cross_summary),indent=2))
