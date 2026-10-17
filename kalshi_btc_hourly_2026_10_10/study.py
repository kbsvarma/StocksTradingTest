"""Offline strike-cushion study; no network, credentials, or order submission."""
import json,gzip,math,sys,pickle,datetime as dt
from pathlib import Path
from collections import Counter,defaultdict
from decimal import Decimal as D,ROUND_CEILING,ROUND_HALF_UP
from zoneinfo import ZoneInfo
import numpy as np
from pull import P,DATA,START,END,ETHSTART,ts,save
sys.path.insert(0,str(P.parent/'kalshi_market_expansion_2026_10_09/extension/source'))
from engine import limit_price
from lanes import LANES,Z2,Z3
ET=ZoneInfo('America/New_York')
ASSETS=('BTC','ETH','SOL','XRP','COPPER','NATGAS','EURUSD','WTI')
PROFILES={
 'late':dict(hi=10,z2=2,z3=3),
 'extended20':dict(hi=20,z2=2,z3=3),
 'extended30':dict(hi=30,z2=2,z3=3),
 'stronger20':dict(hi=20,z2=2.5,z3=3.5),
 'average20':dict(hi=20,z2=2,z3=3,average=True),
 'time20':dict(hi=20,z2=2,z3=3,time=True),
}
SCENARIOS={'fees1c':('opening',.01),'fees2c':('opening',.02),'cap1c':('cap',.01),'delay1c':('delay',.01)}
def etday(t):return dt.datetime.fromtimestamp(t,ET).date().isoformat()
def fee(price,multiplier=1):
 cost=D(str(price))*5
 raw=(D('.07')*D(str(multiplier))*cost*(1-D(str(price)))).quantize(D('.000001'),rounding=ROUND_CEILING)
 return float((cost+raw).quantize(D('.0001'),rounding=ROUND_CEILING)-cost)
def quote(c,phase='c'):
 try:b,a=float(c['b'+phase]),float(c['a'+phase])
 except (TypeError,ValueError,KeyError):return None
 return (b,a) if math.isfinite(b+a) and 0<=b<=a<=1 else None
def stats(bars):
 if len(bars)<61:return {}
 keys=np.arange(min(bars),max(bars)+60,60);v=np.array([bars.get(int(t),np.nan) for t in keys])
 ret=np.diff(np.log(v));w=np.lib.stride_tricks.sliding_window_view(ret,60)
 sig=w.std(axis=1);valid=np.isfinite(w).all(axis=1)&(sig>0)
 return {int(keys[i+60]+60):(float(v[i+60]),float(sig[i])) for i in np.flatnonzero(valid)}
def distance(m,spot,side):
 lo,hi=m.get('floor_strike'),m.get('cap_strike');kind=m['strike_type']
 if kind in ('greater','greater_or_equal'):return side*(spot/float(lo)-1)
 if kind in ('less','less_or_equal'):return -side*(spot/float(hi if hi is not None else lo)-1)
 if kind=='between':
  lo,hi=float(lo),float(hi)
  if side==1:return min(spot/lo-1,1-spot/hi)
  return max(1-spot/lo,spot/hi-1) if spot<lo or spot>hi else -min(spot/lo-1,1-spot/hi)
 raise ValueError('unsupported strike '+str(kind))
def outcome(m,value):
 lo,hi=m.get('floor_strike'),m.get('cap_strike');k=m['strike_type']
 if k=='greater':return value>float(lo)
 if k=='greater_or_equal':return value>=float(lo)
 if k=='less':return value<float(hi if hi is not None else lo)
 if k=='less_or_equal':return value<=float(hi if hi is not None else lo)
 if k=='between':return float(lo)<=value<=float(hi)
 raise ValueError(k)
def build():
 cf={'BTC':{},'ETH':{}};av={'BTC':{},'ETH':{}};duplicates=0
 for row in map(json.loads,(DATA/'benchmark.jsonl').read_text().splitlines()):
  for t,last,mean in row['minutes']:
   if t in cf[row['asset']]:assert abs(cf[row['asset']][t]-last)<1e-8;duplicates+=1
   cf[row['asset']][t]=last;av[row['asset']][t]=mean
 cb={}
 for row in map(json.loads,(DATA/'coinbase_BTC.jsonl').read_text().splitlines()):
  for c in row['response']:cb[int(c[0])]=float(c[4])
 ss={a:stats(bb) for a,bb in cf.items()};ss['CB']=stats(cb)
 allpools={};coverage={};integrity={};eventcounts={}
 for series in ('KXBTCD','KXBTC','KXETHD','KXBTC15M'):
  dest=DATA/series;meta=json.loads((dest/'markets.json').read_text());asset='ETH' if series=='KXETHD' else 'BTC';control=series=='KXBTC15M'
  candles={x['ticker']:{c['t']:c for c in x['candles']} for x in map(json.loads,(dest/'candles.jsonl').read_text().splitlines())}
  sm=json.loads((dest/'series.json').read_text())['response']['series'];assert sm['fee_type']=='quadratic'
  count=Counter();checks={};pools={mode:[] for mode in ('opening','cap','delay')}
  eventcounts[series]=len({m['event_ticker'] for m in meta})
  for m in meta:
   assert m['result'] in ('yes','no') and m['settlement_ts'] and m['price_ranges']
   op,cl=ts(m['open_time']),ts(m['close_time']);settled=ts(m['settlement_ts']);assert settled>=cl
   assert float(m['notional_value_dollars'])==1
   avg=av[asset].get(cl-60)
   if avg is not None and m.get('expiration_value'):
    value=float(D(str(avg)).quantize(D('.01'),rounding=ROUND_HALF_UP));actual=float(m['expiration_value'])
    check=checks.setdefault(m['event_ticker'],dict(event=m['event_ticker'],average=value,expiration=actual,difference=abs(value-actual),outcome_mismatches=[]))
    if outcome(m,value)!=(m['result']=='yes'):check['outcome_mismatches'].append(m['ticker'])
   cs=candles.get(m['ticker'],{});count['markets']+=1
   if not cs:count['no_tape']+=1;continue
   volume={};cum=0
   for t,c in sorted(cs.items()):cum+=float(c.get('v') or 0);volume[t]=cum
   for rem in range(10 if control else 30,2,-1):
    T=cl-rem*60
    if T<op:continue
    pair=quote(cs.get(T))
    if pair is None:count['missing_quote']+=1;continue
    side=1 if sum(pair)>1 else -1;bid,ask=pair if side==1 else (1-pair[1],1-pair[0])
    if ask-bid>.02000001 or not .85-1e-8<=ask<.98-1e-8:continue
    count['band_minutes']+=1
    if not control and volume[T]<5:count['insufficient_prior_volume']+=1;continue
    stat=ss['CB' if control else asset].get(T)
    if stat is None:count['missing_complete_underlying']+=1;continue
    spot,sigma=stat
    if control:
     ref=cb.get(op-60)
     if ref is None:count['missing_reference']+=1;continue
     dist=side*(spot/ref-1)
    else:dist=distance(m,spot,side)
    z=dist/(sigma*math.sqrt(rem));za=dist/(sigma*math.sqrt(rem-2/3+1/60))
    count['feature_minutes']+=1
    for lane,rule in LANES.items():
     if not float(rule.floor)-1e-8<=ask<float(rule.ceiling)-1e-8:continue
     cap=float(limit_price(dict(lane=lane,signal_ask=ask,side='yes' if side==1 else 'no',price_ranges=m['price_ranges']))[1])
     for mode in pools:
      pair=quote(cs.get(T+60),'c' if mode=='delay' else 'o')
      if pair is None:count[mode+'_missing_execution_quote']+=1;continue
      entry=pair[1] if side==1 else 1-pair[0]
      if not float(rule.floor)-1e-8<=entry<=cap+1e-8:count[mode+'_entry_outside_cap']+=1;continue
      if mode=='cap':entry=cap
      paid=fee(entry,sm['fee_multiplier']);won=int((side==1)==(m['result']=='yes'))
      key=m['ticker'] if control else f'{asset}H:{cl}'
      pools[mode].append(dict(ticker=key,market_ticker=m['ticker'],event=m['event_ticker'],series=series,asset=asset,lane=lane,t=T,entered=T+(60 if mode=='delay' else 3),close=cl,settled=settled,side=side,entry=entry,ask=ask,bid=bid,cap=cap,quantity=5,fee=paid,won=won,pnl=5*(won-entry)-paid,rem=rem,z=z,zaverage=za,spot=spot,sigma=sigma,prior_volume=volume[T],mode=mode,priority=0 if control else 1))
  coverage[series]=dict(count);integrity[series]=dict(events=len(checks),unavailable=eventcounts[series]-len(checks),mismatches=[x for x in checks.values() if x['difference']>.011 or x['outcome_mismatches']])
  allpools[series]=pools
  print('BUILD',series,dict(count),{k:len(v) for k,v in pools.items()},flush=True)
 coverage['underlying']={a:dict(minutes=len(bb),complete_stats=len(ss[a]),missing=sum(t not in bb for t in range((ETHSTART if a=='ETH' else START)-7200,END,60))) for a,bb in cf.items()}
 coverage['underlying']['CoinbaseBTC']=dict(minutes=len(cb),complete_stats=len(ss['CB']),missing=sum(t not in cb for t in range(START-7200,END,60)))
 save(P/'coverage.json',coverage);save(P/'integrity.json',integrity);save(P/'event_counts.json',eventcounts)
 with gzip.open(P/'opportunities.json.gz','wt') as f:json.dump(allpools,f,separators=(',',':'))
 return allpools
def select(rows,profile):
 c=PROFILES[profile];out=[]
 for x in rows:
  if not 3<=x['rem']<=c['hi']:continue
  threshold=c['z2'] if x['lane']==Z2 else c['z3']
  if c.get('time'):threshold+=max(0,.5*math.log2(x['rem']/5))
  if x['zaverage' if c.get('average') else 'z']>=threshold:out.append(x)
 return out
def simulate(rows,stress=.01):
 cash={0:50.,2:50.};active=[];closed=[];seen=set();daily=defaultdict(float);ld=defaultdict(float);blocked=Counter()
 route=lambda x:0 if x['asset']=='EURUSD' else 2
 def settle(t):
  nonlocal active
  remaining=[]
  for x in sorted(active,key=lambda x:(x['settled'],x['ticker'])):
   if x['settled']<=t:
    cash[route(x)]+=5*x['won'];daily[etday(x['settled'])]+=x['net'];ld[(etday(x['settled']),x['lane'])]+=x['net'];closed.append(x)
   else:remaining.append(x)
  active=remaining
 def ordering(x):return (x['entered'],x['lane']!=Z2,ASSETS.index(x['asset']),x.get('priority',0),x['ask'],-x['z'],x.get('market_ticker',x['ticker']))
 for x in sorted(rows,key=ordering):
  settle(x['entered'])
  if x['ticker'] in seen:continue
  reserve=5*float(LANES[x['lane']].reserve);projected=reserve+sum(y['reserve'] for y in active);day=etday(x['entered']);reason=''
  if any(y['asset']==x['asset'] for y in active):reason='same_underlying'
  elif 5*(len(active)+1)>25:reason='contracts'
  elif projected>25+1e-8:reason='exposure'
  elif daily[day]-projected < -20-1e-8:reason='daily_including_open'
  elif x['lane']==Z3:
   own=[y for y in active if y['lane']==Z3];risk=reserve+sum(y['reserve'] for y in own)
   if 5*(len(own)+1)>25 or risk>25+1e-8:reason='premium_exposure'
   elif ld[(day,Z3)]-risk < -25-1e-8:reason='premium_daily'
  if not reason and cash[route(x)]<max(5,reserve)-1e-8:reason='cash'
  if reason:blocked[reason]+=1;continue
  assert x['entered']<x['close'] and x['entry']<=x['cap']+1e-8
  net=x['pnl']-5*stress;cash[route(x)]-=5*x['entry']+x['fee']+5*stress
  active.append(dict(x,net=net,reserve=reserve));seen.add(x['ticker'])
 settle(float('inf'))
 assert len(seen)==len(closed)
 return sorted(closed,key=lambda x:(x['settled'],x['ticker'])),dict(blocked)
def metrics(rows):
 equity=peak=dd=0.;daily=defaultdict(float)
 bytime=defaultdict(float)
 for x in rows:
  assert abs(x['net']-(5*(x['won']-x['entry'])-x['fee']-(x['pnl']-x['net'])))<1e-8
  bytime[x['settled']]+=x['net'];daily[etday(x['settled'])]+=x['net']
 for t,net in sorted(bytime.items()):equity+=net;peak=max(peak,equity);dd=max(dd,peak-equity)
 return dict(trades=len(rows),wins=sum(x['won'] for x in rows),losses=sum(1-x['won'] for x in rows),net=round(equity,4),drawdown=round(dd,4),daily={d:round(v,4) for d,v in sorted(daily.items())},lane_counts=dict(Counter(x['lane'] for x in rows)),weekend_trades=sum(dt.datetime.fromtimestamp(x['t'],ET).weekday()>=5 for x in rows))
def periods(start):return [('full',start,END),('early',start,ts('2026-09-26T04:00:00Z')),('middle',max(start,ts('2026-09-26T04:00:00Z')),ts('2026-10-03T04:00:00Z')),('recent',max(start,ts('2026-10-03T04:00:00Z')),END)]
def evaluate(pools):
 results={};ledgers={}
 for series,modes in pools.items():
  profiles=tuple(PROFILES) if series=='KXBTCD' else ('late',) if series=='KXBTC15M' else ('late','extended20')
  for profile in profiles:
   result={}
   for cost,(mode,stress) in SCENARIOS.items():
    candidates=select(modes[mode],profile);result[cost]={}
    for period,lo,hi in periods(ETHSTART if series=='KXETHD' else START):
     accepted,blocks=simulate([x for x in candidates if lo<=x['close']<hi],stress)
     result[cost][period]=dict(**metrics(accepted),blocked=blocks);ledgers[f'{series}/{profile}/{cost}/{period}']=accepted
   results[series+'/'+profile]=result
   print(series,profile,{c:{p:(x['trades'],x['wins'],x['losses'],x['net'],x['drawdown']) for p,x in v.items()} for c,v in result.items()},flush=True)
 save(P/'results.json',results)
 with gzip.open(P/'ledgers.json.gz','wt') as f:json.dump(ledgers,f,separators=(',',':'))
 # Same-source/rules control and combined candidate opportunities, not selected trades.
 integrations={};integration_ledgers={}
 with gzip.open(P.parent/'kalshi_wti_2026_10_07/budget_opportunities.json.gz','rt') as f:old=json.load(f)
 lo,hi=ts('2026-09-23T00:00:00Z'),ts('2026-10-03T00:00:00Z')
 for cost,(mode,stress) in SCENARIOS.items():
  if cost=='delay1c':continue
  base=[dict(x,priority=0) for x in old[cost] if x['asset']!='COPPER' and lo<=x['close']<hi]
  baseline,_=simulate(base,stress);b=metrics(baseline);integrations[cost]={'base_seven':b};integration_ledgers[cost+'/base_seven']=baseline
  for profile in PROFILES:
   extra=[x for x in select(pools['KXBTCD'][mode],profile) if lo<=x['close']<hi]
   rows,blocks=simulate(base+extra,stress);m=metrics(rows)
   kept={x['ticker'] for x in rows if x.get('series')!='KXBTCD'}
   integrations[cost][profile]=dict(**m,hourly_entries=sum(x.get('series')=='KXBTCD' for x in rows),displaced_base_entries=sum(x['ticker'] not in kept for x in baseline),delta_trades=m['trades']-b['trades'],delta_net=round(m['net']-b['net'],4),blocked=blocks)
   integration_ledgers[cost+'/'+profile]=rows
  print('INTEGRATED',cost,{k:(v['trades'],v['net']) for k,v in integrations[cost].items()},flush=True)
 save(P/'integration.json',dict(period=[lo,hi],current_assets_without_copper=True,results=integrations))
 with gzip.open(P/'integration_ledgers.json.gz','wt') as f:json.dump(integration_ledgers,f,separators=(',',':'))
if __name__=='__main__':
 if len(sys.argv)>1 and sys.argv[1]=='reuse':
  with gzip.open(P/'opportunities.json.gz','rt') as f:pools=json.load(f)
 else:pools=build()
 evaluate(pools)
