"""Frozen research rules; no account access or order submission."""
import math,statistics
TICKER='KXSOLPERP'
CONFIG={'mode':'PAPER_ONLY','ticker':TICKER,'entry_channel_hours':24,'exit_channel_hours':12,'history_hours':96,'max_hold_hours':72,'execution_delay_seconds':3600,'max_notional':50.0,'initial_stop_risk':1.0,'taker_fee_rate':.0012,'extra_cost_bp_per_side':1.0,'max_entry_spread_bp':20,'fee_note':'Fixed scenario: account rate verified 2026-09-27UTC; these are modeled fees, not charged fees.'}
def quote(c):
 try:b=float(c['bid']['close']);a=float(c['ask']['close'])
 except (KeyError,TypeError,ValueError):return None
 return (b,a,(b+a)/2) if math.isfinite(a+b) and 0<b<=a<100000 else None

def signal(candles,T):
 cs={c['end_period_ts']:c for c in candles if c['end_period_ts']<=T};qs={s:quote(c) for s,c in cs.items()};q=qs.get(T)
 if not q:return {'status':'missing_decision_quote'}
 hs={s:qs[s][2] for s in range(T-96*3600,T+1,3600) if qs.get(s)}
 if len(hs)<88 or not qs.get(T-3600) or sum(bool(qs.get(s)) for s in range(T-97*3600,T,3600))<88:return {'status':'history_gap'}
 ret=[math.log(p/hs[s-3600]) for s,p in hs.items() if s-3600 in hs]
 if len(ret)<77:return {'status':'return_history_gap'}
 sd=statistics.pstdev(ret);h24=[p for s,p in hs.items() if T-24*3600<=s<T];h12=[p for s,p in hs.items() if T-12*3600<=s<T]
 if sd<=0 or len(h24)<22 or len(h12)<11 or sum(T-48*3600<=s<T for s in hs)<44:return {'status':'insufficient_indicator'}
 side=1 if q[2]>max(h24) else -1 if q[2]<min(h24) else 0
 spread=(q[1]-q[0])/q[2]*10000
 return {'status':'ready','decision_ts':T,'side':side,'mid':q[2],'spread_bp':spread,'active_hour':float(cs[T].get('volume') or 0)>0,'entry_high':max(h24),'entry_low':min(h24),'exit_high':max(h12),'exit_low':min(h12),'stop_fraction':2*sd*math.sqrt(24)}

def size(price,stop):return math.floor(min(CONFIG['max_notional']/price,CONFIG['initial_stop_risk']/(price*stop))) if price>0 and stop>0 else 0

def levels(book,key):
 out=[]
 for p,n in book.get(key,[]):
  p,n=float(p),float(n)
  if math.isfinite(p+n) and 0<p<100000 and n>0:out.append((p,n))
 return sorted(out,reverse=key=='bids')

def vwap(book,side,count):
 remain=count;cost=0
 for p,n in levels(book,'asks' if side==1 else 'bids'):
  take=min(remain,n);cost+=take*p;remain-=take
  if remain<=1e-9:return cost/count
 return None

def funding_amount(position,rates,until):
 import datetime
 total=0
 for f in rates:
  ft=datetime.datetime.fromisoformat(f['funding_time'].replace('Z','+00:00')).timestamp()
  if position['entry_ts']<ft<=until:total-=position['side']*position['count']*float(f['mark_price'])*float(f['funding_rate'])
 return total

def pnl(position,exit_price,funding):
 n=position['count'];ep=position['entry'];gross=position['side']*n*(exit_price-ep);fees=n*(ep+exit_price)*CONFIG['taker_fee_rate'];stress=2*max(n*.0001,n*ep*.0001)
 return {'gross':gross,'fees':fees,'funding':funding,'extra_execution_cost':stress,'net':gross-fees+funding-stress}
