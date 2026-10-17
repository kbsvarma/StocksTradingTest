from pathlib import Path
import json,gzip,math,datetime as dt,bisect
from collections import namedtuple,Counter
from zoneinfo import ZoneInfo
import numpy as np,pandas as pd
P=Path(__file__).resolve().parent;D=P/'data';UTC=dt.timezone.utc;ET=ZoneInfo('America/New_York')
TS=lambda s:int(dt.datetime.fromisoformat(s.replace('Z','+00:00')).timestamp())
START=TS('2026-06-04T00:00:00Z');END=TS('2026-10-10T00:00:00Z')
M={m['ticker']:m for m in json.load(open(D/'markets.json'))['markets']};FEES=json.load(open(D/'fee_receipt.json'))['response']['taker_fee_rates']
Quote=namedtuple('Quote','bid ask mid vol')
def save(n,x):
 if n.endswith('.gz'):
  with gzip.open(P/n,'wt') as f:json.dump(x,f)
 else:(P/n).write_text(json.dumps(x,indent=2)+'\n')
def load(t,period):
 raw=json.load(gzip.open(D/(t+('_1h.json.gz' if period==3600 else '_1m.json.gz')),'rt'))['candlesticks'];rows={}
 for c in raw:
  try:b=float(c['bid']['close']);a=float(c['ask']['close']);v=float(c.get('volume') or 0)
  except (ValueError,TypeError,KeyError):continue
  if not(0<b<=a<1e8) or (a-b)/((a+b)/2)>.005:continue
  rows[int(c['end_period_ts'])]=Quote(b,a,(a+b)/2,v)
 fs=json.load(open(D/(t+'_funding.json')))['funding_rates'];fund={TS(r['funding_time']):(float(r['funding_rate']),float(r['mark_price'])) for r in fs}
 return rows,fund
def split(t):return TS('2026-09-01T00:00:00Z') if M[t]['asset_class']=='Crypto' else TS('2026-10-01T00:00:00Z')
def mr(t,side):
 x=M[t].get('long_leverage_estimates' if side>0 else 'short_leverage_estimates') or M[t].get('leverage_estimates') or {}
 return 1/float(x['1000']) if x.get('1000') else math.inf

def indicators(t,Q,period,kind):
 if not Q:return{}
 step=period if kind=='hour' else 300
 idx=np.arange((min(Q)//step)*step,(max(Q)//step)*step+1,step);f=pd.Series({s:q.mid for s,q in Q.items() if s%step==0}).reindex(idx);lp=np.log(f)
 vol=lp.diff().rolling(72 if kind=='hour' else 288,min_periods=65 if kind=='hour' else 260).std(ddof=0).shift(1)*(1 if kind=='hour' else math.sqrt(12))
 fast=lp.ewm(span=8 if kind=='hour' else 72,min_periods=8 if kind=='hour' else 72,adjust=False).mean();slow=lp.ewm(span=32 if kind=='hour' else 288,min_periods=32 if kind=='hour' else 288,adjust=False).mean();trend=fast-slow
 windows=[12,24,48] if kind=='hour' else[12,48]
 roll={}
 for w in windows:
  x=lp.rolling(w,min_periods=math.ceil(.9*w));half=lp.rolling(max(2,w//2),min_periods=math.ceil(.9*max(2,w//2)))
  roll[w]={'mean':x.mean().shift(1),'sd':x.std(ddof=0).shift(1),'hi':x.max().shift(1),'lo':x.min().shift(1),'exithi':half.max().shift(1),'exitlo':half.min().shift(1)}
 out={}
 for i,s in enumerate(idx):
  if not np.isfinite(lp.iloc[i]+vol.iloc[i]) or vol.iloc[i]<=0:continue
  rr={'price':float(f.iloc[i]),'log':float(lp.iloc[i]),'vol':float(vol.iloc[i]),'trend':float(trend.iloc[i]),'oldtrend':float(trend.iloc[i-1]) if i else 0}
  for w,dd in roll.items():
   z={k:float(v.iloc[i]) for k,v in dd.items()}
   if np.isfinite(sum(z.values())) and z['sd']>0:rr[w]=z
  out[int(s)]=rr
 return out

def signal(t,rule,s,Q,I):
 q=Q.get(s);i=I.get(s)
 if not q or not i or q.vol<=0:return None
 crypto=M[t]['asset_class']=='Crypto';kind='hour' if rule.startswith('h_') else'minute';d=dt.datetime.fromtimestamp(s,ET)
 if kind=='minute' and not crypto and (d.weekday()>4 or not(570<=d.hour*60+d.minute<=900)):return None
 side=0;meta={};stop=0;floor=.005 if crypto else .002
 if rule.startswith('h_channel') or rule.startswith('m_channel'):
  w=int(rule.split('channel')[1]);w=w if kind=='hour' else w//5;b=i.get(w)
  if not b:return None
  side=1 if i['log']>b['hi'] else -1 if i['log']<b['lo'] else 0
  stop=max(floor,2*i['vol']*(math.sqrt(12) if kind=='hour' else 1));meta={'w':w,'family':'channel'}
 elif rule=='h_ema8_32':
  if i['trend']*i['oldtrend']<=0:side=1 if i['trend']>0 else-1
  stop=max(floor,2*i['vol']*math.sqrt(12));meta={'family':'ema'}
 elif 'reversal'in rule:
  w=int(rule.split('reversal')[1]);w=w if kind=='hour' else w//5;b=i.get(w)
  if not b:return None
  z=(i['log']-b['mean'])/b['sd'];side=-1 if z>=2 else 1 if z<=-2 else 0
  stop=max(floor,2*b['sd']);meta={'family':'reversal','mean':b['mean'],'sd':b['sd'],'w':w}
 elif rule=='m_openingrange':
  if d.weekday()>4 or not(600<=d.hour*60+d.minute<=720):return None
  a=int(dt.datetime.combine(d.date(),dt.time(9,30),ET).timestamp());e=a+1800
  qs=[Q.get(k) for k in range(a,e+60,60)]
  if any(x is None for x in qs):return None
  hi=max(x.mid for x in qs);lo=min(x.mid for x in qs);side=1 if q.mid>hi else-1 if q.mid<lo else 0
  stop=max(floor,(hi-lo)/q.mid);meta={'family':'opening','date':d.date().isoformat()}
 if not side or stop>.05:return None
 return dict(side=side,stop=stop,meta=meta,signal=s)

def run(t,rule,Q,F,I,period,start,end,lev=2,slip=1,delay=None,signal_Q=None):
 delay=delay or period;fee=FEES[t];crypto=M[t]['asset_class']=='Crypto';kind='hour' if rule.startswith('h_') else'minute'
 if not Q or start>=end:return {'trades':0,'net':0,'drawdown':0,'status':'no_history'},[],[]
 start=max(start,min(Q));end=min(end,max(Q));start=math.ceil(start/period)*period;end=end//period*period
 if start>=end:return {'trades':0,'net':0,'drawdown':0,'status':'no_history'},[],[]
 # Funding lookup remains correct when quotes or hourly events are absent.
 fts=sorted(F);fv=np.cumsum([F[s][0]*F[s][1] for s in fts]);fund=lambda a,b:float((fv[bisect.bisect_right(fts,b)-1] if bisect.bisect_right(fts,b) else 0)-(fv[bisect.bisect_right(fts,a)-1] if bisect.bisect_right(fts,a) else 0))
 cash=100.;pos=None;pending=None;cool=0;rows=[];path=[];skips=Counter();peak=100.;dd=0.;day=None;daybase=100.;last_eq=100.;used=set();miss=0;mindef=100.;maxmargin=0.
 def mark(s):
  if not pos:return cash,False,None
  q=Q.get(s)
  if not q:return None,False,None
  px=q.bid if pos['side']>0 else q.ask;g=pos['side']*pos['qty']*(px-pos['price']);ff=-pos['side']*pos['qty']*fund(pos['entry'],s);ec=pos['qty']*pos['price']*(fee+slip/10000);coll=pos['margin']+g+ff-ec
  return cash+g+ff-ec-pos['qty']*px*(fee+slip/10000),mr(t,pos['side'])*pos['qty']*q.mid/max(coll,1e-12)>=.85,px
 def close(s,reason):
  nonlocal cash,pos,cool
  if not Q.get(s):return False
  px=Q[s].bid if pos['side']>0 else Q[s].ask;q=pos['qty'];g=pos['side']*q*(px-pos['price']);ff=-pos['side']*q*fund(pos['entry'],s);fees=q*(px+pos['price'])*fee;cost=q*(px+pos['price'])*slip/10000;net=g+ff-fees-cost
  rows.append(dict(pos,exit=s,exit_price=px,gross=g,funding=ff,fees=fees,slippage=cost,net=net,reason=reason));cash+=net;pos=None;cool=s+(3600 if kind=='hour' else 900);return True
 for s in range(start,end+1,period):
  eq,_,_=mark(s)
  if eq is not None:last_eq=eq
  d=dt.datetime.fromtimestamp(s,ET)
  if d.date()!=day:day=d.date();daybase=last_eq
  if pending and s>=pending['at']:
   if pending['kind']=='close':
    if close(s,pending['reason']):pending=None
   else:
    sig=pending['sig'];q=Q.get(s)
    if not q:skips['missing_entry']+=1
    elif (q.ask-q.bid)/q.mid>(.002 if crypto else .001):skips['wide_entry']+=1
    elif cash<=20:skips['cash_floor']+=1
    else:
     side=sig['side'];px=q.ask if side>0 else q.bid;costfrac=2*(fee+slip/10000)+(q.ask-q.bid)/px;risk=min(2,.02*cash);qty=int(min(risk/(px*(sig['stop']+costfrac)),.5*cash*lev/px));margin=qty*px/lev;ec=qty*px*(fee+slip/10000);risk=qty*px*(sig['stop']+costfrac)
     if qty<1:skips['integer_size']+=1
     elif margin<1.3*mr(t,side)*qty*px:skips['initial_margin']+=1
     elif cash-margin-ec<20:skips['cash_reserve']+=1
     elif max(0,daybase-cash)+risk>6:skips['daily_budget']+=1
     else:
      pos=dict(sig,ticker=t,entry=s,price=px,qty=qty,margin=margin,capital=cash,estimated_risk=risk,day_loss=max(0,daybase-cash));maxmargin=max(maxmargin,margin);mindef=min(mindef,cash-margin-ec)
    pending=None
  if pos and not pending:
   eq,breach,px=mark(s);i=I.get(s);reason='';meta=pos['meta']
   if eq is not None:
    ret=pos['side']*(px/pos['price']-1);age=(s-pos['entry'])/60
    if breach:reason='margin_buffer'
    elif ret<=-pos['stop']:reason='stop'
    elif kind=='minute' and (not crypto or meta['family']=='opening') and d.hour*60+d.minute>=955:reason='session_close'
    elif age>=(72*60 if kind=='hour' else 12*60):reason='time'
    elif meta['family']=='reversal':
     if pos['side']*(math.log(px)-meta['mean'])>=0:reason='mean'
     elif abs((math.log(px)-meta['mean'])/meta['sd'])>=4:reason='z4'
     elif kind=='minute' and age>=2*meta['w']*5:reason='time'
    elif meta['family']=='channel' and i and meta['w']in i:
     b=i[meta['w']]
     if pos['side']>0 and math.log(px)<b['exitlo'] or pos['side']<0 and math.log(px)>b['exithi']:reason='channel'
    elif meta['family']=='ema' and i and pos['side']*i['trend']<0:reason='cross'
    elif meta['family']=='opening' and ret>=2*pos['stop']:reason='target'
    if reason:pending={'kind':'close','at':s+delay,'reason':reason}
  if not pos and not pending and s>=cool and s<end-delay and (kind=='hour' or s%300==0):
   sig=signal(t,rule,s,signal_Q if signal_Q is not None else Q,I)
   if sig:
    stampday=d.date().isoformat()
    if sig['meta']['family']=='opening' and stampday in used:skips['one_daily_attempt']+=1
    else:
     if sig['meta']['family']=='opening':used.add(stampday)
     pending={'kind':'open','at':s+delay,'sig':sig}
  eq,_,_=mark(s)
  if eq is not None:
   peak=max(peak,eq);dd=max(dd,peak-eq)
   if s%3600==0:path.append({'t':s,'equity':eq})
  elif pos:miss+=1
 if pos:
  final=max(s for s in Q if pos['entry']<=s<=end)
  if final<=pos['entry']:raise RuntimeError('Unpriceable terminal position')
  close(final,'sample_end')
 path.append({'t':end,'equity':cash});dd=max(dd,peak-cash);weeks={};prev=100.
 for r in path:
  d=dt.datetime.fromtimestamp(r['t'],ET).date();w=(d-dt.timedelta(days=d.weekday())).isoformat();weeks[w]=weeks.get(w,0)+r['equity']-prev;prev=r['equity']
 return {'status':'ok','start':start,'end':end,'days':(end-start)/86400,'trades':len(rows),'wins':sum(r['net']>0 for r in rows),'losses':sum(r['net']<=0 for r in rows),'net':cash-100,'drawdown':dd,'gross':sum(r['gross'] for r in rows),'fees':sum(r['fees'] for r in rows),'funding':sum(r['funding'] for r in rows),'slippage':sum(r['slippage'] for r in rows),'max_trade_loss':min([0]+[r['net'] for r in rows]),'without_best3':sum(r['net'] for r in rows)-sum(sorted([r['net'] for r in rows],reverse=True)[:3]),'margin_exits':sum(r['reason']=='margin_buffer' for r in rows),'max_posted_margin':maxmargin,'min_free_cash':mindef,'missing_held_marks':miss,'weekly':weeks,'positive_weeks':sum(v>0 for v in weeks.values()),'active_weeks':sum(abs(v)>1e-8 for v in weeks.values()),'skips':dict(skips)},rows,path

if __name__=='__main__':
 import sys
 mode=sys.argv[1];kind='hour' if mode.startswith('hour')else'minute';period=3600 if kind=='hour'else 60
 tickers=[t for t in M if t.endswith('PERP')]
 if kind=='minute':tickers=json.load(open(P/'minute_liquidity_selection.json'))['selected']
 rules=['h_channel12','h_channel48','h_ema8_32','h_reversal24'] if kind=='hour'else['m_reversal60','m_reversal240','m_channel60','m_channel240','m_openingrange']
 res={};led={};paths={}
 for t in tickers:
  Q,F=load(t,period);I=indicators(t,Q,period,kind)
  for rule in rules:
   for label,a,b in ([('early',START,split(t))] if mode=='hour_early'else[('later',split(t),END)] if mode=='hour_later'else[('early',START,split(t)),('later',split(t),END)]):
    for slip in [1,5]:
     k=f'{t}/{rule}/{label}/slip{slip}';r,l,p=run(t,rule,Q,F,I,period,a,b,slip=slip);res[k]=r;led[k]=l;paths[k]=p
     print(k,r['trades'],round(r['net'],4),round(r['drawdown'],4),flush=True)
 save(mode+'_results.json',res);save(mode+'_ledgers.json.gz',led);save(mode+'_paths.json.gz',paths)
