"""New lower-fee session/short-horizon rules; isolated research, no orders."""
from pathlib import Path
import sys,json,gzip,math,datetime as dt,itertools,collections,argparse
import numpy as np,pandas as pd
from zoneinfo import ZoneInfo
P=Path(__file__).resolve().parent;OLD=P.parent/'kalshi_perp_smallcap_expansion_2026_10_10';D=OLD/'data';ET=ZoneInfo('America/New_York')
M={x['ticker']:x for x in json.load(open(D/'markets.json'))['markets']};FEES=json.load(open(D/'fee_receipt.json'))['response']['taker_fee_rates']
def ts(s):return int(dt.datetime.fromisoformat(s.replace('Z','+00:00')).timestamp())
END=ts('2026-10-10T00:00:00Z');SPLIT=ts('2026-10-01T00:00:00Z');START=ts('2026-09-10T00:00:00Z')
ASSETS=['KXGOLDPERP','KXSILVERPERP','KXPLATINUMPERP','KXPALLADIUMPERP','KXCOPPERPERP','KXALUMINUMPERP','KXUS500PERP','KXUSTECH100PERP']
def save(name,x):
 if name.endswith('.gz'):
  with gzip.open(P/name,'wt') as f:json.dump(x,f,allow_nan=False)
 else:(P/name).write_text(json.dumps(x,indent=2,allow_nan=False)+'\n')
def load(ticker):
 path=D/(ticker+'_1m.json.gz')
 if not path.exists():return None,[]
 raw=json.load(gzip.open(path,'rt'))['candlesticks'];out=[]
 for c in raw:
  try:b,a=float(c['bid']['close']),float(c['ask']['close']);mid=(b+a)/2;volume=float(c['volume'])
  except (KeyError,ValueError,TypeError):continue
  if not 0<b<=a<1e6 or (a-b)/mid>.005:continue
  def price(field,k):
   try:q=float(c[field][k]);return q if .9*mid<q<1.1*mid else np.nan
   except (KeyError,ValueError,TypeError):return np.nan
  out.append(dict(t=int(c['end_period_ts']),bid=b,ask=a,mid=mid,volume=volume,low=price('bid','low'),high=price('ask','high')))
 if not out:return None,[]
 df=pd.DataFrame(out).drop_duplicates('t').set_index('t').sort_index();df=df.reindex(range(int(df.index.min()),int(df.index.max())+1,60));df['clock']=[dt.datetime.fromtimestamp(int(t),ET).hour*60+dt.datetime.fromtimestamp(int(t),ET).minute for t in df.index];df['date']=[dt.datetime.fromtimestamp(int(t),ET).date().isoformat() for t in df.index];df['weekday']=[dt.datetime.fromtimestamp(int(t),ET).weekday() for t in df.index]
 lp=np.log(df['mid']);df['sigma']=lp.diff().rolling(60,min_periods=60).std(ddof=0)
 for w in [15,30,60]:
  df['mean'+str(w)]=lp.rolling(w,min_periods=w).mean().shift(1);df['sd'+str(w)]=lp.rolling(w,min_periods=w).std(ddof=0).shift(1)
 df['trend']=lp.diff(30)
 active=df['clock'].between(570,955)&(df['weekday']<5);weight=df['volume'].where(active,0).fillna(0);summ=weight.groupby(df['date']).cumsum();wm=(df['mid'].fillna(0)*weight).groupby(df['date']).cumsum()/summ.replace(0,np.nan);wv=(df['mid'].fillna(0)**2*weight).groupby(df['date']).cumsum()/summ.replace(0,np.nan)-wm**2
 df['vwap']=wm;df['vwap_sd']=np.sqrt(wv.clip(lower=0));df['cum_volume']=summ
 fund=json.load(open(D/(ticker+'_funding.json')))['funding_rates'];fund=[(ts(f['funding_time']),float(f['funding_rate'])*float(f['mark_price'])) for f in fund]
 return df,fund
def configs():
 #12 cells:6 short reversion,3 volume-weighted reversion,3 trend pullback.
 return [('short_fade',w,z) for w in [15,30,60] for z in [1.5,2]]+[('vwap_fade',60,z) for z in [1,1.5,2]]+[('vwap_pullback',60,z) for z in [0,.5,1]]
def signal(r,cfg):
 fam,w,zmin=cfg
 if not 600<=r.clock<=900 or r.weekday>=5 or not np.isfinite(r.sigma) or r.volume<1 or r.ask-r.bid>.001*r.mid:return None
 if fam=='short_fade':
  mean=getattr(r,'mean'+str(w));sd=getattr(r,'sd'+str(w));z=(math.log(r.mid)-mean)/sd if sd>0 else np.nan
  if not np.isfinite(z) or abs(z)<zmin:return None
  side=-1 if z>0 else 1;target=math.exp(mean);stop=max(.0008,2*sd);hold=w
 else:
  if not np.isfinite(r.vwap_sd) or r.vwap_sd<=0 or r.cum_volume<20:return None
  z=(r.mid-r.vwap)/r.vwap_sd
  if fam=='vwap_fade':
   if abs(z)<zmin:return None
   side=-1 if z>0 else 1;target=r.vwap;stop=max(.0008,2*r.vwap_sd/r.mid);hold=60
  else:
   side=1 if r.trend>0 else -1
   #Trend seen over30min,entry only after a pullback to the other side of VWAP.
   if abs(r.trend)<r.sigma*math.sqrt(30) or side*z> -zmin:return None
   stop=max(.0008,2*r.sigma*math.sqrt(15));target=r.mid*(1+side*stop*1.5);hold=60
 if stop>.02:return None
 return dict(side=side,target=target,stop=stop,hold=hold,z=float(z),family=fam)
def run(ticker,df,F,cfg,start,end,slip=1,delay=60,capital=25):
 if df is None:return dict(status='no_data',trades=0,net=0),[],[]
 rows=list(df.itertuples());by={int(r.Index):r for r in rows};cash=capital;pos=None;pending=None;done=[];path=[];sk=collections.Counter();cool=0;peak=capital;dd=0;day='';daybase=capital;last_eq=capital;fee=FEES[ticker];rate=fee+slip/10000
 def funding(a,b):return sum(v for t,v in F if a<t<=b)
 def mark(p,r):
  px=r.bid if p['side']>0 else r.ask
  return p['side']*p['qty']*(px-p['price'])-p['side']*p['qty']*funding(p['entry'],int(r.Index))-p['qty']*(px+p['price'])*rate
 def close(t,r,reason):
  nonlocal pos,cash,cool
  px=r.bid if pos['side']>0 else r.ask;gross=pos['side']*pos['qty']*(px-pos['price']);fu=-pos['side']*pos['qty']*funding(pos['entry'],t);fees=pos['qty']*(px+pos['price'])*fee;fric=pos['qty']*(px+pos['price'])*slip/10000;net=gross+fu-fees-fric
  done.append(dict(pos,exit=t,exit_price=px,reason=reason,gross=gross,funding=fu,fees=fees,slippage=fric,net=net,ticker=ticker));cash+=net;pos=None;cool=t+300
 for r in rows:
  t=int(r.Index)
  if t<start or t>=end:continue
  valid=np.isfinite(r.bid+r.ask) and 0<r.bid<=r.ask<1e6
  if r.date!=day:day=r.date;daybase=last_eq
  if pending and t>=pending['at']:
   if pending['type']=='close':
    if valid:close(t,r,pending['reason']);pending=None
    else:sk['missing_exit']+=1
   else:
    sig=pending['signal'];side=sig['side']
    if not valid or r.ask-r.bid>.001*r.mid:sk['entry_quote']+=1
    else:
     px=r.ask if side>0 else r.bid;levmap=M[ticker].get('long_leverage_estimates' if side>0 else 'short_leverage_estimates') or M[ticker].get('leverage_estimates',{});mr=1/float(levmap.get('1000') or .001)
     qty=int(min(.01*min(capital,cash)/(px*(sig['stop']+2*rate+(r.ask-r.bid)/px)),cash/px));margin=qty*px/2;debit=qty*px*rate;estimated=qty*px*(sig['stop']+2*rate+(r.ask-r.bid)/px)
     if side*(sig['target']-px)<=0:sk['target_passed']+=1
     elif qty<1:sk['integer_size']+=1
     elif margin<1.3*mr*qty*px:sk['initial_margin']+=1
     elif cash-margin-debit<capital*.2:sk['reserve']+=1
     elif max(0,daybase-cash)+estimated>capital*.06:sk['daily_risk']+=1
     elif qty>r.volume:sk['entry_volume_proxy']+=1
     else:pos=dict(sig,entry=t,decision=pending['decision'],qty=qty,price=px,margin=margin,mr=mr,capital=cash,estimated_risk=estimated,entry_volume=r.volume)
    pending=None
  if pos and valid and not pending:
   px=r.bid if pos['side']>0 else r.ask;adverse=r.low if pos['side']>0 else r.high;equity=cash+mark(pos,r);col=pos['margin']+mark(pos,r);breach=pos['mr']*pos['qty']*r.mid/max(col,1e-9)>=.85;reason=''
   if breach:reason='margin_buffer'
   elif t>pos['entry'] and np.isfinite(adverse) and pos['side']*(adverse/pos['price']-1)<=-pos['stop']:reason='adverse_minute_stop'
   elif pos['side']*(px/pos['price']-1)<=-pos['stop']:reason='stop'
   elif r.clock>=955:reason='session_close'
   elif pos['side']*(px-pos['target'])>=0:reason='target'
   elif t-pos['entry']>=pos['hold']*60:reason='time'
   if reason:pending=dict(type='close',at=t+delay,reason=reason)
  if pos and not valid:sk['missing_held_marks']+=1
  if not pos and not pending and valid and t>=cool and t%300==0 and t+delay<end:
   sig=signal(r,cfg)
   if sig:pending=dict(type='open',at=t+delay,signal=sig,decision=t)
  eq=cash+mark(pos,r) if pos and valid else cash if not pos else None
  if eq is not None:peak=max(peak,eq);dd=max(dd,peak-eq);last_eq=eq;path.append((t,eq))
 if pos:
  eligible=[r for r in rows if pos['entry']<r.Index<end and np.isfinite(r.bid+r.ask)]
  if eligible:r=eligible[-1];close(int(r.Index),r,'sample_end')
  else:raise RuntimeError('Unpriced final position')
 daily=collections.defaultdict(float)
 for r in done:daily[dt.datetime.fromtimestamp(r['exit'],ET).date().isoformat()]+=r['net']
 days=(end-start)/86400
 return dict(status='ok',trades=len(done),wins=sum(r['net']>0 for r in done),net=cash-capital,drawdown=max(dd,peak-cash),trades_per_calendar_day=len(done)/days,days=days,daily=dict(daily),without_best3=sum(r['net'] for r in done)-sum(sorted([r['net'] for r in done],reverse=True)[:3]),gross=sum(r['gross'] for r in done),fees=sum(r['fees'] for r in done),slippage=sum(r['slippage'] for r in done),funding=sum(r['funding'] for r in done),skips=dict(sk)),done,path
def main(mode):
 data={t:load(t) for t in ASSETS};summ={t:dict(first=int(df.index.min()),last=int(df.index.max()),prior_sessions=len(set(df[(df.index<SPLIT)&(df.volume>0)&(df.weekday<5)].date))) for t,(df,f) in data.items() if df is not None};save('perp_coverage.json',summ);selected=[t for t in ASSETS if summ.get(t,{}).get('prior_sessions',0)>=5];save('perp_eligible_assets.json',selected)
 res={};led={};start,end=(START,SPLIT) if mode=='select' else (SPLIT,END)
 for cfg in configs():
  name='/'.join(map(str,cfg))
  for label,slip,delay in [('base',1,60),('slip5',5,60),('delay120',1,120)]:
   allrows=[];paths=[]
   for t in selected:
    df,f=data[t];r,ll,path=run(t,df,f,cfg,start,end,slip,delay,capital=100/len(selected));key=t+'/'+name+'/'+label;res[key]=r;led[key]=ll;allrows+=ll;paths.append(dict(path))
   #Fixed equal cash allocation per eligible asset. Never sum standalone100s.
   allrows.sort(key=lambda r:r['exit']);net=sum(r['net'] for r in allrows);daily=collections.defaultdict(float)
   for r in allrows:daily[dt.datetime.fromtimestamp(r['exit'],ET).date().isoformat()]+=r['net']
   joint=[];missing=0
   for t in range(start,end,60):
    vals=[100/len(selected) if t<summ[a]['first'] else paths[i].get(t) for i,a in enumerate(selected)]
    if any(v is None for v in vals):missing+=1;continue
    joint.append(sum(vals))
   eq=np.r_[100,joint,100+net];dd=float(np.max(np.maximum.accumulate(eq)-eq));key='POOL/'+name+'/'+label
   res[key]=dict(trades=len(allrows),wins=sum(r['net']>0 for r in allrows),net=net,drawdown=dd,trades_per_calendar_day=len(allrows)/((end-start)/86400),daily=dict(daily),days=(end-start)/86400,missing_joint_minutes=missing,without_best3=net-sum(sorted([r['net'] for r in allrows],reverse=True)[:3]));led[key]=allrows;print(mode,key,len(allrows),round(net,4),round(dd,4),flush=True)
 save('perp_'+mode+'_results.json',res);save('perp_'+mode+'_ledgers.json.gz',led)
 if mode=='select':
  eligible=[('/'.join(map(str,cfg))) for cfg in configs() if all(res['POOL/'+'/'.join(map(str,cfg))+'/'+lab]['net']>0 and res['POOL/'+'/'.join(map(str,cfg))+'/'+lab]['trades_per_calendar_day']>=5 for lab in ['base','slip5','delay120'])];eligible.sort(key=lambda k:res['POOL/'+k+'/base']['net'],reverse=True);save('perp_selection_frozen.json',dict(selected=eligible[0] if eligible else None,passing=eligible,assets=selected,trials=len(configs()),criterion='>=5/calendar day andpositivebase/5bp/delay inpriorperiod'))
if __name__=='__main__':
 ap=argparse.ArgumentParser();ap.add_argument('mode',choices=['select','validate']);a=ap.parse_args();
 if a.mode=='validate':assert (P/'perp_selection_frozen.json').exists()
 main(a.mode)
