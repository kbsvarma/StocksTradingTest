from pathlib import Path
import json,gzip,math,datetime as dt
import numpy as np,pandas as pd
from collections import namedtuple
P=Path(__file__).resolve().parent;D=P/'data';OLD=D/'source_archive'
UTC=dt.timezone.utc
T=lambda s:int(dt.datetime.fromisoformat(s.replace('Z','+00:00')).timestamp())
START=T('2026-06-04T00:00:00Z');SPLIT=T('2026-09-01T00:00:00Z');END=T('2026-10-10T00:00:00Z');JULY=T('2026-07-28T00:00:00Z')
M={m['ticker']:m for m in json.load(open(D/'markets.json'))['markets']}
def save(n,x):
 path=P/n
 if n.endswith('.gz'):
  with gzip.open(path,'wt') as f:json.dump(x,f)
 else:path.write_text(json.dumps(x,indent=2)+'\n')
def load(t):
 raw=json.load(gzip.open(OLD/(t+'_1m.json.gz'),'rt'))
 a=pd.DataFrame.from_dict(raw['rows'],orient='index',columns=raw['cols']);a.index=a.index.astype(int);a=a.sort_index().astype(float)
 # Quotes may be absent or crossed. No future spot or later quote is used to filter them.
 a.loc[(a.bid<=0)|(a.ask<a.bid)|(a.ask>=1e8)|((a.ask-a.bid)/((a.ask+a.bid)/2)>.005)|(~np.isfinite(a.bid+a.ask)),['bid','ask']]=np.nan
 a['mid']=(a.bid+a.ask)/2
 f={T(r['funding_time']):(float(r['funding_rate']),float(r['mark_price'])) for r in json.load(open(OLD/(t+'_funding.json')))}
 return a,f
Q={};F={}
for ticker in ['KXBTCPERP','KXETHPERP']:Q[ticker],F[ticker]=load(ticker)
def mrate(t,side):return 1/float(M[t]['long_leverage_estimates' if side>0 else 'short_leverage_estimates']['1000'])
Quote=namedtuple("Quote","bid ask mid")
QM={t:{int(s):Quote(float(b),float(a),float(m)) for s,b,a,m in zip(q.index,q.bid,q.ask,q.mid) if np.isfinite(b+a) and b>0} for t,q in Q.items()}
def at(t,stamp):return QM[t].get(stamp)

def metrics(path,rows,capital=1000):
 es=np.array([r['equity'] for r in path]);peaks=np.maximum.accumulate(np.r_[capital,es])[1:];draw=(peaks-es)
 daily={}
 for r in path:daily[dt.datetime.fromtimestamp(r['t'],UTC).date().isoformat()]=r['equity']
 weekly={};prev=capital
 for day,e in daily.items():
  d=dt.date.fromisoformat(day);wk=(d-dt.timedelta(days=d.weekday())).isoformat();weekly[wk]=weekly.get(wk,0)+e-prev;prev=e
 return {'trades':len(rows),'wins':sum(r['net']>0 for r in rows),'losses':sum(r['net']<=0 for r in rows),'net':round(es[-1]-capital,6) if len(es) else 0,'return_pct':round(100*(es[-1]/capital-1),6) if len(es) else 0,'drawdown_dollars':round(float(draw.max()),6) if len(es) else 0,'drawdown_pct':round(float(np.max(draw/peaks))*100,6) if len(es) else 0,'fees':round(sum(r['fees'] for r in rows),6),'funding':round(sum(r['funding'] for r in rows),6),'slippage':round(sum(r['slippage'] for r in rows),6),'margin_stops':sum(r['reason']=='margin_buffer' for r in rows),'weekly':weekly,'positive_weeks':sum(v>0 for v in weekly.values()),'active_weeks':sum(abs(v)>1e-9 for v in weekly.values()),'without_best3':round(sum(r['net'] for r in rows)-sum(sorted((r['net'] for r in rows),reverse=True)[:3]),6),'early':round(sum(r['net'] for r in rows if r['entry']<SPLIT),6),'late':round(sum(r['net'] for r in rows if r['entry']>=SPLIT),6)}

class Engine:
 def __init__(self,slip=1):self.cash=1000.;self.pos=None;self.rows=[];self.path=[];self.slip=slip;self.last=None;self.skips={}
 def open(self,stamp,legs,lev,meta):
  # legs are ticker, side, weight. Total intended gross notional=lev*equity.
  if self.pos or self.cash<=0:return
  tmp=[];cash0=self.cash
  for t,side,w in legs:
   r=at(t,stamp)
   if r is None or (r.ask-r.bid)/r.mid>.005:self.skips['entry_bad_quote']=self.skips.get('entry_bad_quote',0)+1;return
   ep=float(r.ask if side>0 else r.bid);qty=int(cash0*lev*w/ep);margin=cash0*w
   if qty<1:return
   fee=qty*ep*.0012;slip=qty*ep*self.slip/10000
   if margin-fee-slip<1.3*mrate(t,side)*qty*ep:self.skips['initial_margin']=self.skips.get('initial_margin',0)+1;return
   tmp.append(dict(ticker=t,side=side,qty=qty,entry=ep,margin=margin,fee=fee,slip=slip,fund=0.))
  self.pos={'entry':stamp,'legs':tmp,'capital':cash0,'meta':meta};self.cash-=sum(l['margin'] for l in tmp)
 def state(self,stamp):
  if not self.pos:return self.cash,False
  equity=self.cash;breach=False
  for l in self.pos['legs']:
   r=at(l['ticker'],stamp)
   if r is None:return None,False
   px=float(r.bid if l['side']>0 else r.ask);eq=l['margin']+l['side']*l['qty']*(px-l['entry'])+l['fund']-l['fee']-l['slip']
   equity+=eq-l['qty']*px*(.0012+self.slip/10000)
   if eq<=0 or mrate(l['ticker'],l['side'])*l['qty']*float(r.mid)/max(eq,1e-9)>=.85:breach=True
  return equity,breach
 def tick(self,stamp):
  if self.pos:
   for l in self.pos['legs']:
    if stamp>self.pos['entry'] and stamp in F[l['ticker']]:
     rate,mark=F[l['ticker']][stamp];l['fund']-=l['side']*l['qty']*mark*rate
  eq,breach=self.state(stamp)
  if eq is not None:self.path.append({'t':stamp,'equity':eq})
  return breach
 def close(self,stamp,reason):
  if not self.pos:return False
  # Reconcile from official timestamps, including payments at the exit minute.
  # This also prevents a missing quote or action/tick ordering from losing funding.
  for l in self.pos['legs']:
   l['fund']=-l['side']*l['qty']*sum(rate*mark for ft,(rate,mark) in F[l['ticker']].items() if self.pos['entry']<ft<=stamp)
  eq,_=self.state(stamp)
  if eq is None:return False
  p=self.pos;fees=slips=gross=fund=0;legs=[]
  for l in p['legs']:
   r=at(l['ticker'],stamp);px=float(r.bid if l['side']>0 else r.ask);f=l['fee']+l['qty']*px*.0012;sl=l['slip']+l['qty']*px*self.slip/10000;g=l['side']*l['qty']*(px-l['entry'])
   fees+=f;slips+=sl;gross+=g;fund+=l['fund'];legs.append(dict(l,exit=px,net=g+l['fund']-f-sl))
  net=gross+fund-fees-slips
  assert abs(eq-(p['capital']+net))<1e-6
  self.rows.append(dict(entry=p['entry'],exit=stamp,capital=p['capital'],net=net,gross=gross,fees=fees,slippage=slips,funding=fund,reason=reason,meta=p['meta'],legs=legs));self.cash=eq;self.pos=None;return True
