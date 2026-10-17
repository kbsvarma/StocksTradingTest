import json,math,statistics,collections,datetime
from pathlib import Path
P=Path(__file__).resolve().parent;D=P.parent/'kalshi_perps_expanded_2026_09_26/data';O=P/'trades';O.mkdir(exist_ok=True)
def load(f):return json.loads(f.read_text())
def ts(s):return int(datetime.datetime.fromisoformat(s.replace('Z','+00:00')).timestamp())
def quote(c):
 try:b=float(c['bid']['close']);a=float(c['ask']['close'])
 except (KeyError,TypeError,ValueError):return None
 return (b,a,(b+a)/2) if 0<b<=a<1e8 else None
M={m['ticker']:m for m in load(D/'markets.json')['markets']};C={t:{c['end_period_ts']:c for c in load(D/(t+'_candles.json'))['candlesticks']} for t in M};Q={t:{s:quote(c) for s,c in cs.items()} for t,cs in C.items()};F={t:[(ts(f['funding_time']),float(f['funding_rate']),float(f['mark_price'])) for f in load(D/(t+'_funding.json'))['funding_rates']] for t in M};END=load(D/'receipt.json')['end_ts'];SPLIT=END-14*86400

def window(t,T,n):return [(s,Q[t][s][2]) for s in range(T-n*3600,T+1,3600) if Q[t].get(s)]
def ema(h,period):
 y=h[0][1];last=h[0][0]
 for s,p in h[1:]:a=1-(1-2/(period+1))**((s-last)/3600);y+=(p-y)*a;last=s
 return y

def indicator(t,T):
 h=window(t,T,96);cur=Q[t].get(T)
 if not cur or len(h)<88 or not Q[t].get(T-3600):return None
 by=dict(h);ret=[math.log(p/by[s-3600]) for s,p in h if s-3600 in by]
 if len(ret)<77:return None
 sd=statistics.pstdev(ret);daily=sd*math.sqrt(24)
 if daily<=0:return None
 old=window(t,T-3600,96)
 if len(old)<88:return None
 h24=[p for s,p in h if T-24*3600<=s<T];h12=[p for s,p in h if T-12*3600<=s<T];h48=[math.log(p) for s,p in h if T-48*3600<=s<T]
 if len(h24)<22 or len(h12)<11 or len(h48)<44:return None
 mean=statistics.mean(h48);st=statistics.pstdev(h48)
 if st<=0:return None
 return {'dailyvol':daily,'ema':ema(h,12)-ema(h,48),'oldema':ema(old,12)-ema(old,48),'hi':max(h24),'lo':min(h24),'exithi':max(h12),'exitlo':min(h12),'mean':mean,'z':(math.log(cur[2])-mean)/st}
I={t:{T:indicator(t,T) for T in C[t]} for t in M}

def leg_pnl(l,T,bp=1):
 t=l['ticker'];q=Q[t].get(T)
 if not q:return None
 xp=q[0] if l['side']==1 else q[1];n=l['count'];ep=l['entry'];rate=.000072 if M[t]['asset_class']=='Metals' else .0012
 fees=n*(ep+xp)*rate;fund=sum(-l['side']*n*m*r for ft,r,m in F[t] if l['entry_ts']<ft<=T);stress=2*max(n*float(M[t]['tick_size']),n*ep*bp/10000)
 return {'net':l['side']*n*(xp-ep)-fees+fund-stress,'fee':fees,'funding':fund,'exit':xp}
def finish(name,rows,skips):
 eq=peak=dd=0
 for r in rows:
  for v in r.pop('marks'):peak=max(peak,eq+v);dd=max(dd,peak-eq-v)
  eq+=r['net']
 out=dict(name=name,trades=len(rows),wins=sum(r['net']>0 for r in rows),net=sum(r['net'] for r in rows),stress5=sum(r['stress5'] for r in rows),early=sum(r['net'] for r in rows if r['entry_ts']<SPLIT),recent=sum(r['net'] for r in rows if r['entry_ts']>=SPLIT),drawdown=dd,fees=sum(r['fees'] for r in rows),funding=sum(r['funding'] for r in rows),skips=dict(skips),without_best3=sum(r['net'] for r in rows)-sum(sorted([r['net'] for r in rows],reverse=True)[:3]))
 (O/(name+'.json')).write_text(json.dumps(rows));return out

def directional(t,rule):
 rows=[];skips=collections.Counter();pos=None;marks=[];busy=0
 for T in sorted(C[t]):
  q=Q[t].get(T);i=I[t].get(T)
  if pos:
   mark=leg_pnl(pos,T)
   if not mark:skips['missing_open_mark']+=1;continue
   marks.append(mark['net']);mid=q[2];s=pos['side'];age=(T-pos['entry_ts'])/3600
   reason=''
   if s*(mark['exit']/pos['entry']-1)<=-pos['stop']:reason='stop'
   elif age>=(24 if rule=='reversal48' else 72):reason='time'
   elif i and rule=='ema12_48' and s*i['ema']<0:reason='cross'
   elif i and rule=='channel24_12' and ((s==1 and mid<i['exitlo']) or (s==-1 and mid>i['exithi'])):reason='channel'
   elif rule=='reversal48' and s*(math.log(mid)-pos['mean'])>=0:reason='mean'
   if reason:
    xp=leg_pnl(pos,T+3600);xp5=leg_pnl(pos,T+3600,5)
    if not xp:skips['missing_exit_quote']+=1;continue
    marks.append(xp['net']);rows.append({'entry_ts':pos['entry_ts'],'exit_ts':T+3600,'net':xp['net'],'stress5':xp5['net'],'fees':xp['fee'],'funding':xp['funding'],'leg':pos,'exit':xp['exit'],'reason':reason,'marks':marks});pos=None;marks=[];busy=T+3600
   continue
  if T<=busy or not q or not i or T+3600>END:continue
  if (q[1]-q[0])/q[2]>.002 or float(C[t][T].get('volume') or 0)<=0:continue
  side=0
  if rule=='ema12_48' and i['ema']*i['oldema']<=0:side=1 if i['ema']>0 else -1
  elif rule=='channel24_12':side=1 if q[2]>i['hi'] else -1 if q[2]<i['lo'] else 0
  elif rule=='reversal48':side=-1 if i['z']>=2 else 1 if i['z']<=-2 else 0
  if not side:continue
  e=Q[t].get(T+3600)
  if not e:skips['missing_entry']+=1;continue
  ep=e[1] if side==1 else e[0];stop=2*i['dailyvol'];n=math.floor(min(50/ep,1/(ep*stop)))
  if n<1:skips['minimum_size']+=1;continue
  pos={'ticker':t,'side':side,'entry':ep,'entry_ts':T+3600,'signal_ts':T,'count':n,'stop':stop,'mean':i['mean']};marks=[]
 if pos:
  stamp=max(s for s in Q[t] if Q[t][s]);x=leg_pnl(pos,stamp);x5=leg_pnl(pos,stamp,5)
  if stamp>pos['entry_ts']:
   rows.append({'entry_ts':pos['entry_ts'],'exit_ts':stamp,'net':x['net'],'stress5':x5['net'],'fees':x['fee'],'funding':x['funding'],'leg':pos,'exit':x['exit'],'reason':'sample_end_liquidation','marks':marks+[x['net']]})
 return finish(t+'_'+rule,rows,skips)
results=[]
for t in M:
 for rule in ['ema12_48','channel24_12','reversal48']:results.append(directional(t,rule))
 print(t,'done',flush=True)
(P/'results.json').write_text(json.dumps(results,indent=2))
