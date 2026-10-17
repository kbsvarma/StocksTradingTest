"""Frozen-grid offline replay. No networking or account/order client."""
import collections as C
import datetime as dt
import gzip,hashlib,json,math,pickle,sys
from pathlib import Path
from dataclasses import replace
from decimal import Decimal as D,ROUND_CEILING
from functools import lru_cache
from zoneinfo import ZoneInfo
import numpy as np
P=Path(__file__).resolve().parent;ROOT=P.parent
sys.path.insert(0,str(P/'production_source'))
from lanes import lane_for,Z2,Z3,route_for
from price_rules import limit_price
ET=ZoneInfo('America/New_York')
ASSETS=('BTC','ETH','SOL','XRP','NATGAS','EURUSD','WTI','USDCAD')
def ts(s):return dt.datetime.fromisoformat(s.replace('Z','+00:00')).timestamp()
START=ts('2026-09-14T04:00:00Z');SPLIT=ts('2026-10-08T04:00:00Z');END=ts('2026-10-14T15:30:00Z')+1
CONFIGS={'current':(0,False),'original_minus_010':(.1,False),'original_minus_025':(.25,False),'both_minus_010':(.1,True),'both_minus_025':(.25,True)}
SCENARIOS={'fees_only':('open',0),'fees1c':('open',.01),'fees2c':('open',.02),'cap1c':('cap',.01),'delay60_1c':('delay60',.01)}
PERIODS={'full':(START,END),'earlier':(START,SPLIT),'recent':(SPLIT,END)}
def save(n,o):(P/n).write_text(json.dumps(o,indent=2,allow_nan=False)+'\n')
@lru_cache(None)
def day(t):return dt.datetime.fromtimestamp(t,ET).date().isoformat()
@lru_cache(None)
def fee(price,mult=1):
 p=D(str(price));cost=5*p;f=(D('.07')*D(str(mult))*cost*(1-p)).quantize(D('.000001'),rounding=ROUND_CEILING)
 return float((cost+f).quantize(D('.0001'),rounding=ROUND_CEILING)-cost)
def quote(c,field,side):
 try:b,a=float(c['b'+field]),float(c['a'+field])
 except (KeyError,TypeError,ValueError):return None
 if not 0<=b<=a<=1:return None
 return (b,a) if side==1 else (1-a,1-b)
def normalize(rows,zero_one=False):
 return [dict(m,result=(1 if m['result']==1 else -1) if zero_one else m['result']) for m in rows]
def load():
 paths={'wti_prior':ROOT/'kalshi_frequency_expansion_2026_10_12/financial_inputs/features.pickle',
        'fx':P/'inputs/fx_features.pickle','recent_noncrypto':ROOT/'kalshi_loss_review_2026_10_14/noncrypto_features.pickle',
        'recent_crypto':ROOT/'kalshi_loss_review_2026_10_14/recent_features.pickle'}
 raw={k:pickle.loads(f.read_bytes()) for k,f in paths.items()}
 prior=normalize(raw['wti_prior'],True);fx=normalize(raw['fx'],True)
 # One frozen source per time interval; no cherry-picking overlapping revisions.
 wti=[m for m in prior if m['asset']=='WTI' and m['close']<SPLIT]+[m for m in raw['recent_noncrypto'] if m['asset']=='WTI' and m['close']>=SPLIT]
 eur=[m for m in fx if m['asset']=='EURUSD']
 recent=[m for m in raw['recent_noncrypto'] if m['asset'] in ('WTI','NATGAS')]+[m for m in fx if m['asset'] in ('EURUSD','USDCAD')]+raw['recent_crypto']
 recent=[m for m in recent if SPLIT<=m['close']<END]
 groups={'WTI':[m for m in wti if START<=m['close']<END],'EURUSD':[m for m in eur if START<=m['close']<END],'portfolio':recent}
 coverage={}
 for name,ms in groups.items():
  assert len(ms)==len({m['ticker'] for m in ms})
  coverage[name]={}
  for asset in sorted({m['asset'] for m in ms}):
   rr=[m for m in ms if m['asset']==asset];n=sum(sum(lane_for(dict(asset=asset,lane=Z2)).min_minutes<=f['rem']<=10 for f in m['features']) for m in rr)
   slots=sum(11-int(lane_for(dict(asset=asset,lane=Z2)).min_minutes) for m in rr)
   coverage[name][asset]=dict(markets=len(rr),feature_minutes=n,expected_minutes=slots,missing_minutes=slots-n,first_open=min(m['start'] for m in rr),last_close=max(m['close'] for m in rr),zero_feature_markets=sum(not m['features'] for m in rr))
 save('input_manifest.json',{k:dict(path=str(f),sha256=hashlib.sha256(f.read_bytes()).hexdigest()) for k,f in paths.items()});save('coverage.json',coverage)
 return groups,eur
def pool_for(markets,changes,mode):
 out=[];counts=C.Counter()
 for m in markets:
  for f in m['features']:
   for lane in (Z2,Z3):
    rule=lane_for(dict(asset=m['asset'],lane=lane));delta,both=changes.get(m['asset'],(0,False))
    if lane==Z2 or both:rule=replace(rule,z=rule.z-delta)
    if not rule.min_minutes<=f['rem']<=rule.max_minutes:continue
    counts['evaluated_lane_minutes']+=1
    if not float(rule.floor)-1e-8<=f['ask']<float(rule.ceiling)-1e-8:counts['price']+=1;continue
    if f['ask']-f['bid']>.02000001:counts['spread']+=1;continue
    threshold=rule.threshold(f['rem'])
    if f['z']<threshold:counts['z']+=1;continue
    counts['qualified_lane_minutes']+=1
    cap=float(limit_price(dict(asset=m['asset'],lane=lane,signal_ask=f['ask'],side='yes' if f['side']==1 else 'no',price_ranges=m['ranges']))[1])
    if f['ask']>cap+1e-8:counts['signal_above_buy_cap']+=1;continue
    pair=quote(m['candles'].get(f['t']+60),'c' if mode=='delay60' else 'o',f['side'])
    if pair is None:counts['missing_subsequent_quote']+=1;continue
    if not float(rule.floor)-1e-8<=pair[1]<=cap+1e-8:counts['subsequent_quote_outside_limit']+=1;continue
    entered=f['t']+(60 if mode=='delay60' else 3)
    if entered>=m['close']:counts['expired']+=1;continue
    price=cap if mode=='cap' else max(f['ask'],pair[1])
    paid=fee(price,m.get('multiplier',1));won=int(f['side']==m['result'])
    out.append(dict(ticker=m['ticker'],asset=m['asset'],lane=lane,t=f['t'],entered=entered,close=m['close'],settled=m['settled'],side=f['side'],quantity=5,entry=price,cap=cap,ask=f['ask'],bid=f['bid'],subsequent_ask=pair[1],floor=float(rule.floor),ceiling=float(rule.ceiling),z=f['z'],threshold=threshold,rem=f['rem'],won=won,fee=paid,multiplier=m.get('multiplier',1),reserve=5*float(rule.reserve),pnl=5*(won-price)-paid))
 return out,dict(counts)
def simulate(rows,friction):
 cash={0:50.,2:50.};active=[];closed=[];seen=set();daily=C.defaultdict(float);ldaily=C.defaultdict(float);blocks=C.Counter();mincash=dict(cash);peakrisk=0
 def settle(t):
  nonlocal active
  for r in sorted(active,key=lambda r:(r['settled'],r['ticker'])):
   if r['settled']<=t:
    cash[route_for(r)]+=5*r['won'];daily[day(r['settled'])]+=r['net'];ldaily[day(r['settled']),r['lane']]+=r['net'];closed.append(r)
  active=[r for r in active if r['settled']>t]
 for r in sorted(rows,key=lambda r:(r['entered'],r['lane']!=Z2,ASSETS.index(r['asset']))):
  settle(r['entered']);owner=(r['asset'],r['close'])
  if owner in seen:continue
  reserve=max(r['reserve'],5*r['entry']+r['fee']+5*friction);risk=reserve+sum(x['reserve'] for x in active);d=day(r['entered']);reason=None
  own=[x for x in active if x['lane']==Z3];ownrisk=reserve+sum(x['reserve'] for x in own)
  if any(x['asset']==r['asset'] for x in active):reason='asset_pending'
  elif 5*(len(active)+1)>25:reason='contracts'
  elif risk>25+1e-8:reason='exposure'
  elif daily[d]-risk < -20-1e-8:reason='daily_including_open'
  elif r['lane']==Z3 and (5*(len(own)+1)>25 or ownrisk>25+1e-8):reason='premium_exposure'
  elif r['lane']==Z3 and ldaily[d,Z3]-ownrisk < -25-1e-8:reason='premium_daily'
  elif cash[route_for(r)]<max(5,reserve)-1e-8:reason='route_cash'
  if reason:blocks[reason]+=1;continue
  x=dict(r,net=r['pnl']-5*friction,friction=5*friction,reserve=reserve,risk_at_entry=risk,open_contracts_at_entry=5*(len(active)+1),daily_realized_at_entry=daily[d],route_cash_before=cash[route_for(r)])
  cash[route_for(r)]-=5*r['entry']+r['fee']+5*friction;active.append(x);seen.add(owner);peakrisk=max(peakrisk,risk)
  mincash[route_for(r)]=min(mincash[route_for(r)],cash[route_for(r)])
 settle(float('inf'));closed.sort(key=lambda r:(r['settled'],r['ticker']))
 assert abs(sum(cash.values())-100-sum(r['net'] for r in closed))<1e-6
 return closed,dict(blocks=dict(blocks),final_cash=cash,minimum_cash=mincash,peakrisk=peakrisk)
def metrics(rows):
 eq=peak=dd=0.;by=C.defaultdict(float);daily=C.defaultdict(float);count=C.Counter()
 for r in rows:by[r['settled']]+=r['net'];daily[day(r['entered'])]+=r['net'];count[day(r['entered'])]+=1
 for t in sorted(by):eq+=by[t];peak=max(peak,eq);dd=max(dd,peak-eq)
 return dict(trades=len(rows),wins=sum(r['won'] for r in rows),losses=sum(1-r['won'] for r in rows),net=round(eq,4),fees=round(sum(r['fee'] for r in rows),4),friction=round(sum(r['friction'] for r in rows),4),drawdown=round(dd,4),daily=dict(sorted(daily.items())),daily_trades=dict(sorted(count.items())))
def difference(rows,baseline):
 b={r['ticker']:r for r in baseline};n={r['ticker']:r for r in rows};added=[r for k,r in n.items() if k not in b];removed=[r for k,r in b.items() if k not in n]
 retimed=[r for k,r in n.items() if k in b and any(r[v]!=b[k][v] for v in ('t','lane','side','entry'))]
 return dict(extra_trades=len(rows)-len(baseline),extra_losses=sum(1-r['won'] for r in rows)-sum(1-r['won'] for r in baseline),net_change=round(sum(r['net'] for r in rows)-sum(r['net'] for r in baseline),4),added=metrics(added),removed=metrics(removed),retimed=len(retimed),added_tickers=[r['ticker'] for r in added],removed_tickers=[r['ticker'] for r in removed])
def main():
 groups,fx_all=load();results={};ledgers={};checks=C.Counter()
 for group,ms in groups.items():
  results[group]={};ledgers[group]={}
  configs=CONFIGS if group!='portfolio' else {'current':{},**{a.lower()+'_'+k:{a:v} for a in ('WTI','EURUSD') for k,v in CONFIGS.items() if k!='current'},**{'both_assets_'+k:{a:v for a in ('WTI','EURUSD')} for k,v in CONFIGS.items() if k!='current'}}
  for name,cfg in configs.items():
   changes={group:cfg} if group!='portfolio' else cfg;results[group][name]={};ledgers[group][name]={}
   for scenario,(mode,friction) in SCENARIOS.items():
    pool,counts=pool_for(ms,changes,mode);results[group][name][scenario]={};ledgers[group][name][scenario]={}
    periods=PERIODS if group!='portfolio' else {'recent':PERIODS['recent']}
    for period,(lo,hi) in periods.items():
     rows,diag=simulate([r for r in pool if lo<=r['close']<hi],friction);base=rows if name=='current' else ledgers[group]['current'][scenario][period]
     results[group][name][scenario][period]=dict(**metrics(rows),**diag,comparison=difference(rows,base),lane_counts=dict(C.Counter(r['lane'] for r in rows)),counts=counts)
     ledgers[group][name][scenario][period]=rows
     for r in rows:
      assert r['t']<r['entered']<r['close']<=r['settled'] and r['quantity']==5
      assert r['floor']-1e-8<=r['entry']<=r['cap']+1e-8
      assert r['z']>=r['threshold'] and r['ask']-r['bid']<=.02000001
      assert r['open_contracts_at_entry']<=25 and r['risk_at_entry']<=25+1e-8 and r['daily_realized_at_entry']-r['risk_at_entry']>=-20-1e-8
      assert abs(r['net']-(5*(r['won']-r['entry'])-r['fee']-r['friction']))<1e-8
      checks['validated_ledger_rows']+=1
     assert len(rows)==len({(r['asset'],r['close']) for r in rows})
  print(group,{k:{a:results[group][k]['fees1c'][a]['trades'] for a in results[group][k]['fees1c']} for k in configs},flush=True)
 save('results.json',results)
 with gzip.open(P/'ledgers.json.gz','wt') as f:json.dump(ledgers,f,separators=(',',':'))
 # Reproduce the prior finance baseline on its exact horizon and execution convention.
 pp,_=pool_for([m for m in fx_all if START<=m['close']<ts('2026-10-14T16:00:00Z')],{},'open');rr,_=simulate(pp,.01)
 prior=json.loads((ROOT/'kalshi_finance_expansion_2026_10_14/fx/results.json').read_text())['eurusd_adaptive']['quote1c']['EURUSD']['full']
 got=metrics(rr);assert all(abs(got[k]-prior[k])<1e-4 for k in ('trades','wins','losses','net')),(got,prior)
 save('validation.json',dict(passed=True,checks=dict(checks),prior_eurusd_baseline={k:got[k] for k in ('trades','wins','losses','net')},configurations=CONFIGS))
 print('VALIDATED',checks,flush=True)
if __name__=='__main__':main()
