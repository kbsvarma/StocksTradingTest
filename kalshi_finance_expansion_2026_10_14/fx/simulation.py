"""Frozen metal mechanisms, causal weekly probabilities and quote-proxy portfolios."""
import argparse,datetime as dt,gzip,hashlib,json,math,pickle
from pathlib import Path
from decimal import Decimal as D,ROUND_CEILING,ROUND_FLOOR
from collections import defaultdict,Counter
from functools import lru_cache
from zoneinfo import ZoneInfo
import numpy as np
from pull import P,ASSETS,START,END,ts
from build import quote
ET=ZoneInfo('America/New_York')
PERIODS={'development':(START,ts('2026-09-21T00:00:00Z')),'selection':(ts('2026-09-21T00:00:00Z'),ts('2026-10-03T00:00:00Z')),'confirmation':(ts('2026-10-03T00:00:00Z'),ts('2026-10-08T00:00:00Z')),'recent':(ts('2026-10-08T00:00:00Z'),END),'pooled':(START,END)}
def save(n,x):(P/n).write_text(json.dumps(x,indent=2,allow_nan=False)+'\n')
@lru_cache(None)
def day(t):return dt.datetime.fromtimestamp(t,ET).date().isoformat()
@lru_cache(None)
def fee(px,qty=5,multiplier=1,precision='.0001'):
 p=D(str(px));cost=p*qty;raw=(D('.07')*D(str(multiplier))*qty*p*(1-p)).quantize(D('.000001'),rounding=ROUND_CEILING);unit=D(precision)
 return float(((cost+raw)/unit).to_integral_value(rounding=ROUND_CEILING)*unit-cost)
@lru_cache(None)
def cap(ask,side,absolute,ranges):
 spend=min(D(str(ask))+D('.01'),D(str(absolute)));yes=spend if side==1 else 1-spend
 for row in json.loads(ranges):
  lo,hi,step=map(D,(row['start'],row['end'],row['step']))
  if lo<=yes<=hi:
   px=lo+((yes-lo)/step).to_integral_value(rounding=ROUND_FLOOR if side==1 else ROUND_CEILING)*step
   return float(px if side==1 else 1-px)
 raise ValueError('missing tick range')

def portfolio(rows,stress=.01,enforce=True):
 cash=100.;active=[];closed=[];seen=set();daily=defaultdict(float);blocked=Counter();minimum_cash=cash;peak_risk=0
 def settle(t):
  nonlocal cash,active
  remaining=[]
  for r in sorted(active,key=lambda r:(r['settled'],r['ticker'])):
   if r['settled']<=t:cash+=5*r.get('payout',r['won']);daily[day(r['settled'])]+=r['net'];closed.append(r)
   else:remaining.append(r)
  active=remaining
 for r in sorted(rows,key=lambda r:(r['entered'],0 if r['lane']=='original' else 1,ASSETS.index(r['asset']))):
  settle(r['entered'])
  if r['ticker'] in seen:continue
  reserve=max(r['reserve'],5*r['entry']+r['fee']+stress*5)
  risk=sum(a['reserve'] for a in active)+reserve;reason=None
  if enforce:
   if any(a['asset']==r['asset'] for a in active):reason='asset_owned'
   elif len(active)*5+5>25 or risk>25+1e-8:reason='exposure'
   elif daily[day(r['entered'])]-risk < -20-1e-8:reason='daily_including_open'
   elif cash<max(5,reserve):reason='cash'
  if reason:blocked[reason]+=1;continue
  net=r['net_before_friction']-stress*5;cash-=5*r['entry']+r['fee']+stress*5;minimum_cash=min(minimum_cash,cash);peak_risk=max(peak_risk,risk)
  active.append(dict(r,net=net,friction=stress*5,reserve=reserve));seen.add(r['ticker'])
 settle(float('inf'))
 return sorted(closed,key=lambda r:(r['settled'],r['ticker'])),dict(blocked=blocked,minimum_cash=minimum_cash,peak_reserved_risk=peak_risk,final_cash=cash)


def metrics(rows):
 by=defaultdict(float);daily=defaultdict(float);weeks=defaultdict(float)
 for r in rows:by[r['settled']]+=r['net'];daily[day(r['settled'])]+=r['net'];weeks[dt.date.fromisoformat(day(r['settled'])).isocalendar()[:2]]+=r['net']
 eq=peak=dd=0
 for t in sorted(by):eq+=by[t];peak=max(peak,eq);dd=max(dd,peak-eq)
 return dict(trades=len(rows),wins=sum(r['won'] for r in rows),losses=sum(1-r['won'] for r in rows),net=round(eq,6),drawdown=round(dd,6),mean_cost=sum(r['entry'] for r in rows)/len(rows) if rows else None,breakeven=sum(r['entry']+(r['fee']+r['friction'])/5 for r in rows)/len(rows) if rows else None,positive_days=sum(v>0 for v in daily.values()),active_days=len(daily),positive_weeks=sum(v>0 for v in weeks.values()),active_weeks=len(weeks),daily=dict(sorted(daily.items())),without_best_day=eq-max(daily.values(),default=0),without_best3=eq-sum(sorted((r['net'] for r in rows),reverse=True)[:3]))


