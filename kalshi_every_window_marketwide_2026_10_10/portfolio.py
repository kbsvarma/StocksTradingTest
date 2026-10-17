"""Offline common execution/risk and diagnostics. No network or order API."""
import math,statistics,gzip,json
from collections import Counter,defaultdict
from datetime import datetime,timedelta
from zoneinfo import ZoneInfo
from common import P,read,save,ts
ET=ZoneInfo('America/New_York')
QTY=5
SCENARIOS=(('base',.01,'open'),('cost2c',.02,'open'),('delay60',.01,'close'),('cap',.01,'cap'))
def day(t):return datetime.fromtimestamp(t,ET).date().isoformat()
def week(t):
 d=datetime.fromtimestamp(t,ET).date();return (d-timedelta(days=(d.weekday()-5)%7)).isoformat()
def fee(p,m=1):return math.ceil((.07*m*5*p*(1-p)-1e-12)*10000)/10000

def quote(c,side='yes',phase='close'):
 try:b=float(c['yes_bid'][phase+'_dollars']);a=float(c['yes_ask'][phase+'_dollars'])
 except (TypeError,KeyError,ValueError):return None
 if not (math.isfinite(a) and math.isfinite(b) and 0<=b<=a<=1):return None
 return (b,a) if side=='yes' else (1-a,1-b)
def payout(m,side):
 if m.get('result') in ('yes','no'):return int(m['result']==side)
 v=m.get('settlement_value_dollars')
 if v is None:return None
 return float(v) if side=='yes' else 1-float(v)
def execute(s,mode):
 q=quote(s['_cs'].get(s['t']+60),s['side'],'close' if mode=='close' else 'open')
 if not q:return None
 entry=max(s['ask'],q[1]);enter=s['t']+(60 if mode=='close' else 3)
 if entry>s['cap']+1e-8 or entry<=0 or enter>=min(s['close'],s.get('game_end',s['close'])):return None
 if mode=='cap':entry=s['cap']
 m=s['_market'];pay=payout(m,s['side'])
 if pay is None or not m.get('settlement_ts'):return None
 settled=ts(m['settlement_ts'])
 if settled<=enter:return None
 return {**{k:v for k,v in s.items() if not k.startswith('_')},'entered':enter,'entry':entry,'payout':pay,'won':pay==1 if pay in (0,1) else None,'settled':settled,'fee':fee(entry,s['_mult'])}
def portfolio(rows,stress=.01,one_asset=False,enforce=True):
 cash=100.;real=defaultdict(float);active=[];seen=set();out=[];blocked=Counter();mincash=cash;maxrisk=0
 for r in sorted(rows,key=lambda x:(x['entered'],x.get('priority',0),x['asset'],x['event'],x['ticker'],x['side'])):
  for a in sorted(active,key=lambda x:(x['settled'],x['event'])):
   if a['settled']<=r['entered']:cash+=5*a['payout'];real[day(a['settled'])]+=a['net']
  active=[a for a in active if a['settled']>r['entered']]
  owner=(r['asset'],r['close']) if r['kind']=='finance' else r.get('game_owner',r['event'])
  if owner in seen:continue
  cost=5*r['entry']+r['fee']+5*stress;reserve=cost;risk=sum(a['reserve'] for a in active)+reserve
  why=''
  if enforce:
   if one_asset and any(a['asset']==r['asset'] for a in active):why='asset_pending'
   elif len(active)>=5:why='capacity'
   elif risk>25+1e-8:why='exposure'
   elif real[day(r['entered'])]-risk < -20-1e-8:why='daily_open_risk'
   elif cash-cost<2:why='cash'
  if why:blocked[why]+=1;continue
  rr=dict(r,net=5*(r['payout']-r['entry'])-r['fee']-5*stress,reserve=reserve)
  seen.add(owner);cash-=cost;mincash=min(mincash,cash);maxrisk=max(maxrisk,risk);active.append(rr);out.append(rr)
 return out,dict(blocked,min_cash=round(mincash,5),peak_reserved=round(maxrisk,5))
def metrics(rows):
 eq=peak=dd=0;ds=defaultdict(float);ws=defaultdict(float);byasset=defaultdict(list)
 for r in sorted(rows,key=lambda x:(x['settled'],x['event'])):
  eq+=r['net'];peak=max(peak,eq);dd=max(dd,peak-eq);ds[day(r['entered'])]+=r['net'];ws[week(r['entered'])]+=r['net'];byasset[r['asset']].append(r)
 return {'trades':len(rows),'wins':sum(r['won'] is True for r in rows),'losses':sum(r['won'] is False for r in rows),'scalar':sum(r['won'] is None for r in rows),'net':round(eq,5),'drawdown':round(dd,5),'weekly':{k:round(v,5) for k,v in sorted(ws.items())},'daily':{k:round(v,5) for k,v in sorted(ds.items())},'positive_weeks':sum(v>0 for v in ws.values()),'active_weeks':len(ws),'without_best_day':round(eq-max(ds.values(),default=0),5),'without_best3':round(eq-sum(sorted([r['net'] for r in rows],reverse=True)[:3]),5),'mean_break_even':sum(r['entry']+r['fee']/5+(5*(r['payout']-r['entry'])-r['fee']-r['net'])/5 for r in rows)/len(rows) if rows else None}
def record(results,ledgers,key,rows,stress,one_asset=False,split=None):
 a,b=portfolio(rows,stress,one_asset);u,_=portfolio(rows,stress,False,False);m=metrics(a);m['blocked']=b;m['unconstrained']=metrics(u);m['assets']={asset:metrics([r for r in a if r['asset']==asset]) for asset in sorted({r['asset'] for r in a})}
 if split:
  m['first_half']=metrics([r for r in a if r['entered']<split]);m['second_half']=metrics([r for r in a if r['entered']>=split])
 results[key]=m;ledgers[key]=a
 return m
