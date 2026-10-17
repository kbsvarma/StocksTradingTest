"""Offline execution-accounting helpers. No exchange client."""
import datetime as dt,json,math
from collections import defaultdict,Counter
from decimal import Decimal as D,ROUND_CEILING,ROUND_FLOOR
from functools import lru_cache
from zoneinfo import ZoneInfo
ET=ZoneInfo('America/New_York')
@lru_cache(None)
def day(t):return dt.datetime.fromtimestamp(t,ET).date().isoformat()
@lru_cache(None)
def fee(px,mult=1):
 p=D(str(px));cost=5*p;raw=(D('.07')*D(str(mult))*5*p*(1-p)).quantize(D('.000001'),rounding=ROUND_CEILING)
 return float((cost+raw).quantize(D('.0001'),rounding=ROUND_CEILING)-cost)
def cap(ask,side,absolute,ranges):
 spend=min(D(str(ask))+D('.01'),D(str(absolute)));yes=spend if side==1 else 1-spend
 for row in ranges:
  lo,hi,step=map(D,(row['start'],row['end'],row['step']))
  if lo<=yes<=hi:
   px=lo+((yes-lo)/step).to_integral_value(rounding=ROUND_FLOOR if side==1 else ROUND_CEILING)*step
   return float(px if side==1 else 1-px)
 raise ValueError('missing price range')
def portfolio(rows,stress=.01):
 cash=100.;active=[];out=[];seen=set();daily=defaultdict(float);blocked=Counter();minimum_cash=100.;peak_risk=0.
 def settle(t):
  nonlocal cash,active
  for r in sorted(active,key=lambda r:(r['settled'],r['ticker'])):
   if r['settled']<=t:cash+=5*r['won'];daily[day(r['settled'])]+=r['net']
  active=[r for r in active if r['settled']>t]
 for r in sorted(rows,key=lambda r:(r['entered'],r.get('priority',0),r['asset'],r['ticker'])):
  settle(r['entered']);owner=(r['asset'],r['close'])
  if owner in seen:continue
  cost=5*r['entry']+r['fee']+stress*5;reserve=max(r.get('reserve',5),cost);risk=sum(x['reserve'] for x in active)+reserve
  reason=None
  if any(x['asset']==r['asset'] for x in active):reason='asset_pending'
  elif len(active)>=5 or risk>25+1e-8:reason='exposure'
  elif daily[day(r['entered'])]-risk < -20-1e-8:reason='daily_including_open'
  elif cash<max(5,reserve):reason='cash'
  if reason:blocked[reason]+=1;continue
  x=dict(r,net=5*(r['won']-r['entry'])-r['fee']-stress*5,friction=stress*5,reserve=reserve)
  cash-=cost;active.append(x);out.append(x);seen.add(owner);minimum_cash=min(minimum_cash,cash);peak_risk=max(peak_risk,risk)
 settle(float('inf'))
 assert abs(cash-100-sum(r['net'] for r in out))<1e-6
 return out,dict(blocked=dict(blocked),final_cash=cash,minimum_cash=minimum_cash,peak_reserved_risk=peak_risk)
def metrics(rows):
 days=defaultdict(float);counts=Counter();by=defaultdict(float);weeks=defaultdict(float)
 for r in rows:
  by[r['settled']]+=r['net'];d=day(r['entered']);days[d]+=r['net'];counts[d]+=1;weeks[str(dt.date.fromisoformat(d).isocalendar()[:2])]+=r['net']
 eq=peak=dd=0
 for t in sorted(by):eq+=by[t];peak=max(peak,eq);dd=max(dd,peak-eq)
 return dict(trades=len(rows),wins=sum(r['won'] for r in rows),losses=sum(1-r['won'] for r in rows),net=round(eq,6),drawdown=round(dd,6),daily=dict(sorted(days.items())),daily_trades=dict(sorted(counts.items())),weekly=dict(sorted(weeks.items())),positive_weeks=sum(v>0 for v in weeks.values()),active_days=len(days),breakeven=sum(r['entry']+(r['fee']+r['friction'])/5 for r in rows)/len(rows) if rows else None,without_best_day=eq-max(days.values(),default=0))
