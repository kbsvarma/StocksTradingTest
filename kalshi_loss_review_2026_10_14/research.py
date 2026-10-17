"""Offline parameter replay. No network calls, order client or production writes."""
from pathlib import Path
from collections import Counter,defaultdict
from decimal import Decimal as D,ROUND_CEILING
from datetime import datetime
from zoneinfo import ZoneInfo
from functools import lru_cache
import json,gzip,pickle,math,sys,argparse
import numpy as np
P=Path(__file__).resolve().parent;ROOT=P.parent
sys.path.insert(0,str(P/'production_source'))
from price_rules import limit_price
from lanes import lane_for
Z2='cushion_z2';Z3='premium_z3';CRYPTO=('BTC','ETH','SOL','XRP');ASSETS=CRYPTO+('COPPER','NATGAS','EURUSD','WTI')
ET=ZoneInfo('America/New_York')
def ts(s):return datetime.fromisoformat(s.replace('Z','+00:00')).timestamp()
@lru_cache(None)
def day(t):return datetime.fromtimestamp(t,ET).date().isoformat()
def save(n,o):(P/n).write_text(json.dumps(o,indent=2,allow_nan=False)+'\n')
def readgz(p):
 with gzip.open(p,'rt') as f:return json.load(f)
CANDIDATES=['baseline']+[a+'_z'+z for a in ('btc','crypto') for z in ('225','250','300')]+[a+'_max'+t for a in ('btc','crypto') for t in ('7','5')]+['btc_z250_max7','btc_floor90','btc_ceiling93','btc_time_z','wti_z175','wti_z200','wti_z250','natgas_z225','natgas_z250','crypto_correlated1','crypto_correlated2']
def keep(x,name):
 if name=='baseline' or x['lane']!=Z2:return True
 a=x['asset'];r=x['rem'];z=x['z'];ask=x['ask']
 target=name.split('_')[0]
 if target=='crypto' and a not in CRYPTO or target!='crypto' and a.lower()!=target:return True
 if name.endswith('correlated1') or name.endswith('correlated2'):return True
 if name.endswith('time_z'):return z+1e-10>=max(2,2+.5*math.log2(r/5))
 if name.endswith('floor90'):return ask>=.90-1e-10
 if name.endswith('ceiling93'):return ask<.93-1e-10
 if '_z' in name and z+1e-10<int(name.split('_z')[1][:3])/100:return False
 if '_max' in name and r>int(name.rsplit('max',1)[1]):return False
 return True
def metrics(rr):
 cum=peak=dd=0
 for x in sorted(rr,key=lambda x:(x['settled'],x['ticker'])):
  cum+=x['net'];peak=max(peak,cum);dd=max(dd,peak-cum)
 return dict(trades=len(rr),wins=sum(x['won'] for x in rr),losses=sum(1-x['won'] for x in rr),net=round(sum(x['net'] for x in rr),4),drawdown=round(dd,4))
def simulate(rows,stress=.01,correlation_cap=None):
 cash={0:50.,2:50.};active=[];closed=[];seen=set();blocks=Counter();daily=defaultdict(float);ldaily=defaultdict(float)
 def route(a):return 0 if a=='EURUSD' else 2
 def settle(t):
  nonlocal active
  rest=[]
  for x in sorted(active,key=lambda r:(r['settled'],r['ticker'])):
   if x['settled']<=t:
    cash[route(x['asset'])]+=5*x['won'];daily[day(x['settled'])]+=x['net'];ldaily[(day(x['settled']),x['lane'])]+=x['net'];closed.append(x)
   else:rest.append(x)
  active=rest
 for x in sorted(rows,key=lambda r:(r['entered'],r['lane']!=Z2,ASSETS.index(r['asset']))):
  settle(x['entered'])
  if x['ticker'] in seen:continue
  risk=4.75 if x['lane']==Z2 else 5.;projected=risk+sum(y['reserve'] for y in active);d=day(x['entered']);reason=''
  if any(y['asset']==x['asset'] for y in active):reason='asset'
  elif correlation_cap is not None and x['asset'] in CRYPTO and sum(y['asset'] in CRYPTO and y['side']==x['side'] and y['close']==x['close'] for y in active)>=correlation_cap:reason='crypto_correlation'
  elif 5*(len(active)+1)>25:reason='contracts'
  elif projected>25+1e-8:reason='exposure'
  elif daily[d]-projected < -20-1e-8:reason='daily_including_open_risk'
  elif x['lane']==Z3:
   own=[y for y in active if y['lane']==Z3];ownrisk=risk+sum(y['reserve'] for y in own)
   if 5*(len(own)+1)>25 or ownrisk>25+1e-8:reason='z3_exposure'
   elif ldaily[(d,Z3)]-ownrisk < -25-1e-8:reason='z3_daily'
  if not reason and cash[route(x['asset'])]<max(5,risk)-1e-8:reason='route_cash'
  if reason:blocks[reason]+=1;continue
  cash[route(x['asset'])]-=5*x['entry']+x['fee']+5*stress
  active.append(dict(x,net=x['pnl']-5*stress,reserve=risk));seen.add(x['ticker'])
 settle(float('inf'))
 return sorted(closed,key=lambda x:(x['settled'],x['ticker'])),dict(blocks)
def compare(pool,stress=.01):
 out={};ledgers={}
 for name in CANDIDATES:
  r,b=simulate([x for x in pool if keep(x,name)],stress,int(name[-1]) if 'correlated' in name else None)
  ledgers[name]=r;out[name]=dict(**metrics(r),blocked=b,by_asset={a:metrics([x for x in r if x['asset']==a]) for a in ASSETS if any(x['asset']==a for x in r)})
  base={x['ticker']:x for x in ledgers['baseline']};new={x['ticker']:x for x in r}
  removed=[x for k,x in base.items() if k not in new];added=[x for k,x in new.items() if k not in base]
  out[name].update(removed=metrics(removed),added=metrics(added),retimed=sum(k in base and (base[k]['t'],base[k]['lane'],base[k]['side'])!=(x['t'],x['lane'],x['side']) for k,x in new.items()))
 return out,ledgers
def actual():
 rows=readgz(P/'orders.json.gz');settled=[]
 for r in rows:
  if r['status']!='settled':continue
  s=r['signal'];won=int(r['side']==r['result'])
  assert D(r['pnl'])==D(r['filled'])*won-D(r['cost'])-D(r['fees'])
  settled.append(dict(r,won=won,net=float(r['pnl']),settled=r['close_ts'],lane=r.get('lane',Z2),ask=s['signal_ask'],z=s['z'],rem=s['remaining_minutes'],close=r['close_ts']))
 results={}
 for label,start in [('all','2026-09-12'),('oct3','2026-10-03'),('oct8','2026-10-08'),('oct9','2026-10-09'),('oct12','2026-10-12')]:
  rr=[x for x in settled if x['settled_day']>=start]
  m=dict(total=metrics(rr),by_asset={a:metrics([x for x in rr if x['asset']==a]) for a in ASSETS},by_lane={l:metrics([x for x in rr if x['lane']==l]) for l in (Z2,Z3)},by_day={d:metrics([x for x in rr if x['settled_day']==d]) for d in sorted({x['settled_day'] for x in rr})},pruning_not_replay={})
  for name in CANDIDATES:
   kept=[];c=Counter()
   for x in sorted(rr,key=lambda x:x['created_ts']):
    if not keep(x,name):continue
    if 'correlated' in name and x['asset'] in CRYPTO:
     key=(x['close'],x['side'])
     if c[key]>=int(name[-1]):continue
     c[key]+=1
    kept.append(x)
   removed=[x for x in rr if x not in kept]
   m['pruning_not_replay'][name]=dict(**metrics(kept),removed=metrics(removed),by_asset={a:metrics([x for x in kept if x['asset']==a]) for a in ASSETS},removed_tickers=[x['ticker'] for x in removed])
  results[label]=m
 results['losses']=[{k:x.get(k) for k in ('ticker','asset','lane','side','filled','entry','cost','fees','pnl','settled_day','z','rem','ask','created_ts','close')} for x in settled if not x['won']]
 results['btc_z2_buckets']={}
 for name,func in [('remaining_minutes',lambda x:str(x['rem'])),('z',lambda x:'2-2.25' if x['z']<2.25 else '2.25-2.5' if x['z']<2.5 else '2.5-3' if x['z']<3 else '3+')]:
  rr=[x for x in settled if x['asset']=='BTC' and x['lane']==Z2 and x['settled_day']>='2026-10-03'];groups=defaultdict(list)
  for x in rr:groups[func(x)].append(x)
  results['btc_z2_buckets'][name]={k:metrics(v) for k,v in groups.items()}
 save('actual_results.json',results)
 return results
def prior():
 pp=readgz(P/'prior_opportunities.json.gz');controls=[];out={};ledgers={}
 old=json.loads((ROOT/'kalshi_loss_diagnosis_2026_10_08/historical_results.json').read_text())
 periods={'matched10d':('2026-09-23T00:00:00Z','2026-10-03T00:00:00Z'),'first5d':('2026-09-23T00:00:00Z','2026-09-28T00:00:00Z'),'second5d':('2026-09-28T00:00:00Z','2026-10-03T00:00:00Z'),'later_noncrypto':('2026-10-03T00:00:00Z','2026-10-08T00:00:00Z')}
 for per,(lo,hi) in periods.items():
  out[per]={};ledgers[per]={}
  for sc,stress in [('fees1c',.01),('fees2c',.02),('cap1c',.01)]:
   allpool=[x for x in pp[sc] if ts(lo)<=x['close']<ts(hi)]
   control,_=simulate(allpool,stress);want=old[per][sc]['baseline'];got=metrics(control)
   for k in ('trades','wins','losses','net'):assert abs(got[k]-want[k])<1e-7,(per,sc,k,got,want)
   controls.append(dict(period=per,scenario=sc,passed=True))
   pool=[x for x in allpool if x['asset']!='COPPER']
   out[per][sc],ll=compare(pool,stress)
   if sc=='fees1c':ledgers[per]=ll
 save('prior_results.json',out);save('prior_controls.json',controls)
 with gzip.open(P/'prior_replay_ledgers.json.gz','wt') as f:json.dump(ledgers,f)
 return out
def fee(px,mult=1):
 p=D(str(px));cost=5*p;trade=(D('.07')*D(str(mult))*cost*(1-p)).quantize(D('.000001'),rounding=ROUND_CEILING)
 return float((cost+trade).quantize(D('.0001'),rounding=ROUND_CEILING)-cost)
def quote(c,field='c',side=1):
 try:b,a=float(c['b'+field]),float(c['a'+field])
 except (KeyError,TypeError,ValueError):return None
 if not (0<=b<=a<=1):return None
 return (b,a) if side==1 else (1-a,1-b)
def pool_for(ms,mode):
 out=[]
 for m in ms:
  for f in m['features']:
   for lane in (Z2,Z3):
    rule=lane_for(dict(asset=m['asset'],lane=lane));floor=float(rule.floor);ceiling=float(rule.ceiling);zmin=rule.threshold(f['rem'])
    if not rule.min_minutes<=f['rem']<=rule.max_minutes:continue
    if not (floor-1e-8<=f['ask']<ceiling-1e-8 and f['ask']-f['bid']<=.02000001 and f['z']>=zmin):continue
    cap=float(limit_price(dict(asset=m['asset'],lane=lane,signal_ask=f['ask'],side='yes' if f['side']==1 else 'no',price_ranges=m['ranges']))[1])
    pair=quote(m['candles'].get(f['t']+60),'c' if mode=='delay60' else 'o',f['side'])
    if pair is None:continue
    px=pair[1]
    if px<floor-1e-8 or px>cap+1e-8:continue
    if mode=='cap':px=cap
    entered=f['t']+(60 if mode=='delay60' else 3)
    if entered>=m['close']:continue
    ff=fee(px,m.get('multiplier',1));won=int(f['side']==m['result'])
    out.append(dict(ticker=m['ticker'],asset=m['asset'],lane=lane,t=f['t'],entered=entered,close=m['close'],settled=m['settled'],side=f['side'],entry=px,cap=cap,quantity=5,fee=ff,won=won,pnl=5*(won-px)-ff,ask=f['ask'],bid=f['bid'],rem=f['rem'],z=f['z']))
 return out
def build_recent():
 data=P/'recent_data';markets=[];cov={};revisions=[]
 for a in CRYPTO:
  prices={};q=Counter()
  for b in map(json.loads,(data/f'spot_{a}.jsonl').read_text().splitlines()):
   for row in b['rows']:
    t=int(row[0]);v=float(row[4]);assert v>0 and t%60==0
    if t in prices and prices[t]!=v:revisions.append([a,t,prices[t],v])
    prices[t]=v
  cc={r['ticker']:{int(c['t']):c for c in r['candles']} for r in map(json.loads,(data/f'candles_{a}.jsonl').read_text().splitlines())}
  series=json.loads((data/f'series_{a}.json').read_text())['response']['series'];assert series['fee_type']=='quadratic'
  for raw in json.loads((data/f'markets_{a}.json').read_text()):
   start,close=ts(raw['open_time']),ts(raw['close_time'])
   if not ts('2026-10-03T00:00:00Z')<=close<=ts('2026-10-14T15:30:00Z'):continue
   assert close-start==900 and raw['result'] in ('yes','no')
   m=dict(ticker=raw['ticker'],asset=a,start=start,close=close,settled=ts(raw['settlement_ts']),result=1 if raw['result']=='yes' else -1,ranges=raw['price_ranges'],candles=cc.get(raw['ticker'],{}),multiplier=float(series['fee_multiplier']),features=[])
   q['markets']+=1
   for rem in range(10,2,-1):
    T=int(close)-rem*60;pair=quote(m['candles'].get(T));keys=range(T-3660,T,60);ref=prices.get(int(start)-60)
    if pair is None:q['missing_quote']+=1;continue
    if ref is None or any(k not in prices for k in keys):q['missing_source']+=1;continue
    values=np.array([prices[k] for k in keys]);sigma=float(np.std(np.diff(np.log(values))))
    if sigma<=0:q['zero_sigma']+=1;continue
    side=1 if sum(pair)>1 else -1
    f=dict(t=T,rem=rem,side=side,bid=pair[0] if side==1 else 1-pair[1],ask=pair[1] if side==1 else 1-pair[0],z=side*(float(values[-1])/ref-1)/(sigma*math.sqrt(rem)),ref=ref,spot=float(values[-1]),sigma=sigma)
    m['features'].append(f);q['features']+=1
   markets.append(m)
  cov[a]=dict(q)
 save('recent_feature_coverage.json',dict(coverage=cov,revisions=revisions,causal_bars='61 completed Coinbase minutes ending T-1 second; no interpolation'))
 (P/'recent_features.pickle').write_bytes(pickle.dumps(markets));return markets
def recent():
 ms=build_recent();out={};ledgers={};pools={}
 for label,mode,stress in [('fees1c','opening',.01),('fees2c','opening',.02),('cap1c','cap',.01),('delay60_1c','delay60',.01)]:
  pool=pool_for(ms,mode);pools[label]=pool;out[label]={};ledgers[label]={}
  for per,lo,hi in [('all','2026-10-03T00:00:00Z','2026-10-14T15:30:01Z'),('oct3_7','2026-10-03T00:00:00-04:00','2026-10-08T00:00:00-04:00'),('oct8_on','2026-10-08T00:00:00-04:00','2026-10-14T15:30:01Z')]:
   subset=[x for x in pool if ts(lo)<=x['close']<ts(hi)]
   out[label][per],ledgers[label][per]=compare(subset,stress)
 save('recent_results.json',out)
 for n,x in [('recent_ledgers',ledgers),('recent_pools',pools)]:
  with gzip.open(P/(n+'.json.gz'),'wt') as f:json.dump(x,f)
 return out
def older_crypto():
 ms=pickle.loads((ROOT/'kalshi_btc_eth_quant_2026_10_02/features.pickle').read_bytes())
 out={};ledgers={};controls=[]
 prior_pools=readgz(P/'prior_opportunities.json.gz')
 for label,mode,stress in [('fees1c','opening',.01),('fees2c','opening',.02),('cap1c','cap',.01),('delay60_1c','delay60',.01)]:
  pool=pool_for(ms,mode);out[label]={};ledgers[label]={}
  if label in prior_pools:
   current=[x for x in pool if ts('2026-09-23T00:00:00Z')<=x['close']<ts('2026-10-03T00:00:00Z')]
   original=[x for x in prior_pools[label] if x['asset'] in CRYPTO and ts('2026-09-23T00:00:00Z')<=x['close']<ts('2026-10-03T00:00:00Z')]
   c,_=simulate(current,stress);o,_=simulate(original,stress)
   assert metrics(c)==metrics(o),(label,metrics(c),metrics(o))
   controls.append(dict(scenario=label,passed=True,metrics=metrics(c)))
  for per,lo,hi in [('aug13_sep10','2026-08-13T00:00:00Z','2026-09-11T00:00:00Z'),('sep11_22','2026-09-11T00:00:00Z','2026-09-23T00:00:00Z'),('sep23_oct2','2026-09-23T00:00:00Z','2026-10-03T00:00:00Z')]:
   out[label][per],ledgers[label][per]=compare([x for x in pool if ts(lo)<=x['close']<ts(hi)],stress)
 save('older_crypto_results.json',out);save('older_crypto_controls.json',controls)
 with gzip.open(P/'older_crypto_ledgers.json.gz','wt') as f:json.dump(ledgers,f)
 return out
def build_noncrypto():
 data=P/'noncrypto_data';prices={a:{} for a in ('NATGAS','WTI','EURUSD')};revisions=[];records=[];cov={}
 for p in sorted((data/'charts').glob('*.json.gz')):
  with gzip.open(p,'rt') as f:d=json.load(f)['response']['live_data']['details']
  a=d['asset']
  if a not in prices:continue
  for x in d['timeseries']:
   t=int(x['t']);v=float(x['v'])
   if t%60000!=59000:continue
   minute=t//60000*60
   if minute in prices[a] and prices[a][minute]!=v:revisions.append([a,minute,prices[a][minute],v])
   prices[a][minute]=v
 assert not revisions,revisions[:3]
 for a,vmap in prices.items():
  q=Counter();cc={r['ticker']:{int(c['t']):c for c in r['candles']} for r in map(json.loads,(data/f'candles_{a}.jsonl').read_text().splitlines())}
  series=json.loads((data/f'series_{a}.json').read_text())['response']['series'];assert series['fee_type']=='quadratic'
  for raw in json.loads((data/f'markets_{a}.json').read_text()):
   start,close=ts(raw['open_time']),ts(raw['close_time'])
   if not ts('2026-10-08T04:00:00Z')<=close<=ts('2026-10-14T15:30:00Z'):continue
   assert close-start==900 and raw['result'] in ('yes','no')
   m=dict(ticker=raw['ticker'],asset=a,start=start,close=close,settled=ts(raw['settlement_ts']),result=1 if raw['result']=='yes' else -1,ranges=raw['price_ranges'],candles=cc.get(raw['ticker'],{}),multiplier=float(series['fee_multiplier']),features=[])
   q['markets']+=1
   for rem in range(10,0 if a=='EURUSD' else 2,-1):
    T=int(close)-rem*60;pair=quote(m['candles'].get(T));keys=range(T-3660,T,60);ref=vmap.get(int(start)-60)
    if pair is None:q['missing_quote']+=1;continue
    if ref is None or any(k not in vmap for k in keys):q['missing_source']+=1;continue
    values=np.array([vmap[k] for k in keys]);sigma=float(np.std(np.diff(np.log(values))))
    if sigma<=0:q['zero_sigma']+=1;continue
    side=1 if sum(pair)>1 else -1
    m['features'].append(dict(t=T,rem=rem,side=side,bid=pair[0] if side==1 else 1-pair[1],ask=pair[1] if side==1 else 1-pair[0],z=side*(float(values[-1])/ref-1)/(sigma*math.sqrt(rem)),ref=ref,spot=float(values[-1]),sigma=sigma));q['features']+=1
   records.append(m)
  cov[a]=dict(q)
 save('noncrypto_feature_coverage.json',dict(coverage=cov,revisions=revisions,causal_bars='61 completed minute endpoints from Pyth event charts; no interpolation'))
 (P/'noncrypto_features.pickle').write_bytes(pickle.dumps(records));return records
def all_recent():
 noncrypto=build_noncrypto();crypto=pickle.loads((P/'recent_features.pickle').read_bytes())
 ms=[m for m in noncrypto+crypto if m['close']>=ts('2026-10-08T04:00:00Z')];out={};ledgers={}
 for label,mode,stress in [('fees1c','opening',.01),('fees2c','opening',.02),('cap1c','cap',.01),('delay60_1c','delay60',.01)]:
  pool=pool_for(ms,mode);out[label],ledgers[label]=compare(pool,stress)
 save('all_recent_results.json',out)
 with gzip.open(P/'all_recent_ledgers.json.gz','wt') as f:json.dump(ledgers,f)
 return out
if __name__=='__main__':
 parser=argparse.ArgumentParser();parser.add_argument('--recent',action='store_true');parser.add_argument('--older-crypto',action='store_true');parser.add_argument('--all-recent',action='store_true');args=parser.parse_args()
 a=actual();h=prior();print('Actual since Oct8',a['oct8']['total']);print('prior baseline',h['matched10d']['fees1c']['baseline'])
 if args.recent:recent();print('RECENT DONE')
 if args.older_crypto:older_crypto();print('OLDER DONE')
 if args.all_recent:all_recent();print('ALL RECENT DONE')
