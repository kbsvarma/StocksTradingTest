from pathlib import Path
import math,json,gzip,pickle,collections,datetime,sys
import numpy as np
from decimal import Decimal as D,ROUND_CEILING,ROUND_FLOOR
from functools import lru_cache
P=Path(__file__).resolve().parent;R=P.parent
ASSETS=('BTC','ETH','SOL','XRP','COPPER','NATGAS','EURUSD','WTI','USDCAD');CRYPTO=ASSETS[:4]
SPLIT=datetime.datetime.fromisoformat('2026-10-08T00:00:00-04:00').timestamp()
def save(n,x):(P/n).write_text(json.dumps(x,indent=2,allow_nan=False)+'\n')
def rules():
 families=[('drop',[.25,.5,.75,1,1.5,2]),('absolute',[0,.5,1,1.5]),('gate',[0,.5,1]),('fraction',[.25,.5,.75]),('price',[.05,.10,.15,.20])]
 return [dict(id=f'{f}_{v:g}_g{g}_p{p}',family=f,value=v,grace=g,persist=p) for f,vv in families for v in vv for g in [0,30,60,120] for p in [0,10,30,60]]
@lru_cache(maxsize=200000)
def fee_sale(q,p):
 q=D(str(q));p=D(str(p));gross=q*p;fee=(D('.07')*q*p*(1-p)).quantize(D('.000001'),rounding=ROUND_CEILING)
 cash=(gross-fee).quantize(D('.0001'),rounding=ROUND_FLOOR);return float(cash),float(gross-cash)
def quote(m,t,side,suffix):
 c=m['candles'].get(int(t))
 if not c:return np.nan
 try:v=float(c['b'+suffix] if side==1 else 1-float(c['a'+{'h':'l','l':'h'}.get(suffix,suffix)]))
 except (ValueError,KeyError,TypeError):return np.nan
 return v if 0<=v<=1 else np.nan
def required(a,lane,rem):
 premium=lane=='premium_z3'
 if a in ('EURUSD','USDCAD'):return max(.5,(2.75 if premium else 1.75)+.5*math.log2(rem/5))
 if a in ('WTI','COPPER'):return 2.5 if premium else 1.5
 return 3 if premium or a=='BTC' else 2
def threshold(r,c):
 if c['family']=='drop':return r['entry_z']-c['value']
 if c['family']=='absolute':return c['value']
 if c['family']=='gate':return r['entry_gate']-c['value']
 if c['family']=='fraction':return r['entry_z']*c['value']
 return r['cost']/r['quantity']-c['value']
def path(r,data,interval=60,seconds=None):
 m=data['markets'][r['ticker']];times=np.arange((int(r['entered'])//interval+1)*interval,int(r['close'])-1,interval,dtype=int)
 out=dict(t=times,z=[],bc=[],low=[],high=[],opening=[],exit_t=[])
 anchor=None
 if seconds is not None and r['asset'] in CRYPTO:
  start,values=seconds[r['asset']];j=int(r['t'])-1-start;anchor=values[j] if 0<=j<len(values) else np.nan
 for t in times:
  boundary=int(t)//60*60;rem=(r['close']-t)/60
  if seconds is None:
   # Completed-minute control: no manufactured intra-minute source variation.
   src=data['stats'][r['asset']].get(boundary);z=r['side']*(src[0]/r['reference']-1)/(src[1]*math.sqrt((r['close']-boundary)/60)) if src else np.nan
  else:
   start,values=seconds[r['asset']];j=int(t)-1-start;spot=values[j] if 0<=j<len(values) else np.nan
   if r['asset'] in CRYPTO:spot=r['entry_spot']*spot/anchor
   z=r['side']*(spot/r['reference']-1)/(r['entry_sigma']*math.sqrt(rem))
  out['z'].append(z);out['bc'].append(quote(m,boundary,r['side'],'c'))
  # Whole-minute high/low bound contains the modeled execution time, but is NOT
  # an actual synchronized executable quote or a depth guarantee.
  execution=t+1;end=(execution//60+1)*60
  out['low'].append(quote(m,end,r['side'],'l'));out['high'].append(quote(m,end,r['side'],'h'))
  op=quote(m,(t//60+1)*60,r['side'],'o');bc=quote(m,boundary,r['side'],'c')
  out['opening'].append(min(op,bc) if np.isfinite(op+bc) else np.nan);out['exit_t'].append(execution)
 for k,v in out.items():out[k]=np.array(v)
 return out
def first_trigger(r,c,p):
 valid=p['t']>=r['entered']+c['grace'];obs=p['bc'] if c['family']=='price' else p['z'];breach=valid&np.isfinite(obs)&(obs<threshold(r,c));idx=np.arange(len(breach));lastclear=np.maximum.accumulate(np.where(~breach,idx,-1));elapsed=(idx-lastclear-1)*(p['t'][1]-p['t'][0] if len(idx)>1 else 0)
 choices=np.flatnonzero(breach&(elapsed>=c['persist']));return int(choices[0]) if len(choices) else None
def exit_result(r,c,p,mode='minute',friction=.01):
 i=first_trigger(r,c,p);out=dict(ticker=r['ticker'],asset=r['asset'],lane=r['lane'],won=r['won'],quantity=r['quantity'],hold=r['hold_net'],net=r['hold_net'],delta=0,exit=False,trigger=None,exit_fee=0,friction=0,price=None,source_missing=int(np.isnan(p['z']).sum()),quote_missing=False,realized=r['settled'])
 if i is None:return out
 out['trigger']=int(p['t'][i]);out['trigger_z']=float(p['z'][i]) if np.isfinite(p['z'][i]) else None;out['threshold']=threshold(r,c);px=p['opening' if mode=='minute' else mode][i]
 if not np.isfinite(px):out['quote_missing']=True;return out
 if px<=0:return out
 cash,fee=fee_sale(r['quantity'],float(px));net=cash-r['quantity']*friction-r['cost']-r['entry_fee']-r.get('entry_friction',0)
 out.update(net=net,delta=net-r['hold_net'],exit=True,exit_fee=fee,friction=r['quantity']*friction,price=float(px),realized=int(p['exit_t'][i]))
 return out
def metrics(rr):
 loss=[x for x in rr if not x['won']];win=[x for x in rr if x['won']];e=peak=dd=0
 for x in sorted(rr,key=lambda r:(r['realized'],r['ticker'])):e+=x['net'];peak=max(peak,e);dd=max(dd,peak-e)
 return dict(trades=len(rr),wins=len(win),losses=len(loss),hold=sum(r['hold'] for r in rr),net=sum(r['net'] for r in rr),delta=sum(r['delta'] for r in rr),losses_reduced=sum(r['delta']>1e-8 for r in loss),losses_fully_avoided=sum(r['net']>=0 for r in loss),recovered=sum(r['delta'] for r in loss),winners_cut=sum(r['exit'] for r in win),winners_turned_loss=sum(r['net']<0 for r in win),winner_cost=-sum(r['delta'] for r in win),exit_fees=sum(r['exit_fee'] for r in rr),friction=sum(r['friction'] for r in rr),quote_missing=sum(r['quote_missing'] for r in rr),source_missing=sum(r['source_missing']>0 for r in rr),drawdown=dd)
