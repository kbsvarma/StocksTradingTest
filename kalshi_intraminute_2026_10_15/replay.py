"""Offline causal entry replay. No network, credentials, or order transport."""
from pathlib import Path
from collections import Counter,defaultdict
from decimal import Decimal as D,ROUND_CEILING
from datetime import datetime,timezone
from zoneinfo import ZoneInfo
import argparse,bisect,gzip,json,math,pickle,sys,hashlib
import numpy as np
P=Path(__file__).resolve().parent;R=P.parent
sys.path.insert(0,str(P/'source'))
from lanes import lane_for,Z2,Z3
from price_rules import limit_price
ASSETS=('BTC','ETH','SOL','XRP');ET=ZoneInfo('America/New_York')
START=int(datetime(2026,9,4,tzinfo=timezone.utc).timestamp());END=START+7*86400
def save(name,x):(P/name).write_text(json.dumps(x,indent=2,allow_nan=False)+'\n')
def day(t):return datetime.fromtimestamp(t,ET).date().isoformat()
def fee_fill(book,side,cap,floor):
 levels=book[2] if side==1 else book[1];left=D(5);cost=D(0);fees=D(0);taken=[]
 for bid,q in sorted(levels,reverse=True):
  px=D(1)-D(str(bid));qty=D(str(q))
  if qty<=0:continue
  if px>D(str(cap))+D('0.00000001'):break
  n=min(left,qty);cost+=n*px
  fees+=(D('.07')*n*px*(1-px)).quantize(D('.000001'),rounding=ROUND_CEILING)
  taken.append([float(px),float(n)]);left-=n
  if left==0:
   if cost/5<D(str(floor))-D('0.00000001'):return None
   debit=(cost+fees).quantize(D('.0001'),rounding=ROUND_CEILING)
   return dict(entry=float(cost/5),cost=float(cost),fee=float(debit-cost),levels=taken)
 return None
def hist_cf():
 start=START-3600;arrs={a:np.full(END-start,np.nan) for a in ASSETS};meta={}
 for path in sorted((P/'cf_seconds').glob('*.gz')):
  a=path.name.split('_')[0];data=json.load(gzip.open(path,'rt'));rows=data['response']['data']['payload']
  for r in rows:
   if r['time']%1000:continue
   i=r['time']//1000-start
   if 0<=i<len(arrs[a]):arrs[a][i]=float(r['value'])
 for a,v in arrs.items():meta[a]=dict(expected_seconds=len(v),present_seconds=int(np.isfinite(v).sum()))
 save('historical_source_coverage.json',meta)
 return start,arrs
def source_fn(bundle,source_delay):
 if 'hist' in bundle:
  origin,vals=bundle['hist']
  def value(t):
   i=int(math.floor(t))-origin
   return float(vals[i]) if 0<=i<len(vals) and np.isfinite(vals[i]) else None
  def spot(t,f):
   T=f['t']
   if t<=T or t<T-1+source_delay:return f['spot']
   anchor=value(T-1);v=value(t-source_delay)
   return f['spot']*v/anchor if anchor and v else None
  return spot
 cf=bundle['cf'];recv=[r[0] for r in cf]
 def spot(t,f):
  T=f['t']
  if t<=T:return f['spot']
  i=bisect.bisect_right(recv,t)-1
  j=bisect.bisect_right(recv,T)-1
  if i<0 or j<0 or t-cf[i][1]>2 or T-cf[j][1]>2:return None
  return f['spot']*cf[i][2]/cf[j][2]
 return spot
def opportunities(bundle,step,delay,quote_mode='cached'):
 m=bundle['market'];books=bundle['books'];bts=[r[0] for r in books];stats=Counter();out=[]
 fs={int(f['t']):f for f in m['features']};getspot=source_fn(bundle,delay)
 tried=set();dense='cf' in bundle;first=bundle.get('first',m['start']);last=bundle.get('last',m['close'])
 # Matched coverage: every mode starts at the same first available full minute.
 lo=max(int(m['close']-600),math.ceil(first/60)*60);hi=int(m['close']-180)
 def z_at(t,f):
  v=getspot(t,f)
  return None if v is None else f['side']*(v/f['ref']-1)/(f['sigma']*math.sqrt((m['close']-t)/60))
 for t in range(lo,hi+1,step):
  T=t//60*60;f=fs.get(T)
  if not f:stats['missing_minute_input']+=1;continue
  if quote_mode=='stream' and step!=60:
   k=bisect.bisect_right(bts,t)-1
   if k<0 or t-bts[k]>(.5 if dense else 2):stats['missing_fresh_quote']+=1;continue
   bk=books[k]
   if not bk[1] or not bk[2]:stats['one_sided_book']+=1;continue
   yb=max(p for p,q in bk[1]);ya=1-max(p for p,q in bk[2])
   if yb>ya+1e-8:stats['crossed_quote']+=1;continue
   side=1 if yb+ya>1 else -1
   f=dict(f,side=side,bid=yb if side==1 else 1-ya,ask=ya if side==1 else 1-yb)
  z=f['z'] if step==60 else z_at(t,f)
  if z is None:stats['missing_fast_source']+=1;continue
  for lane in (Z2,Z3):
   key=(T,lane)
   if key in tried:continue
   rule=lane_for(dict(asset=m['asset'],lane=lane));threshold=rule.threshold((m['close']-t)/60)
   if not (float(rule.floor)-1e-8<=f['ask']<float(rule.ceiling)-1e-8 and f['ask']-f['bid']<=.02000001 and z>=threshold):continue
   tried.add(key);stats['qualified_attempts']+=1
   signal=dict(asset=m['asset'],lane=lane,signal_ask=f['ask'],side='yes' if f['side']==1 else 'no',price_ranges=m['ranges'])
   cap=float(limit_price(signal)[1]);ncheck=0;nextcheck=t+delay;end=min(t+25,last)
   j=bisect.bisect_left(bts,nextcheck);hadbook=False
   while j<len(books) and bts[j]<=end and ncheck<24:
    book=books[j];bt=book[0]
    if dense and bt-nextcheck>.5:
     stats['dense_book_gap']+=1;break
    hadbook=True;ncheck+=1;stats['book_checks']+=1
    # Production freezes all signal inputs throughout the bounded retry episode.
    # Observe current z for diagnosis only; do not add a new execution filter.
    observed_fill_z=z if step==60 else z_at(bt,f)
    fillz=z
    if fillz>=threshold:
     fill=fee_fill(book,f['side'],cap,float(rule.floor))
     if fill:
      won=int(f['side']==m['result'])
      out.append(dict(ticker=m['ticker'],asset=m['asset'],lane=lane,t=t,minute=T,entered=bt,close=m['close'],settled=max(m['close']+30,m['settled']),side=f['side'],quantity=5,won=won,ask=f['ask'],bid=f['bid'],cap=cap,z=z,fill_z=fillz,observed_fill_z=observed_fill_z,rem=(m['close']-t)/60,wait=bt-t,book_index=j,checks=ncheck,net=round(5*won-fill['cost']-fill['fee'],8),**fill))
      stats['executable_attempts']+=1;break
    nextcheck=bt+1
    j=bisect.bisect_left(bts,nextcheck,j+1)
   if not hadbook:stats['no_observed_book_within_deadline']+=1
 stats['qualified_markets']=int(stats['qualified_attempts']>0)
 return out,stats
def simulate(pool,stress=0):
 cash=float(json.loads((P/'preservation_before.json').read_text())['status']['account']['balance'])
 active=[];closed=[];seen=set();blocks=Counter();daily=defaultdict(float);ldaily=defaultdict(float);maxrisk=maxcount=0
 def settle(t):
  nonlocal active,cash
  rest=[]
  for x in sorted(active,key=lambda r:(r['settled'],r['ticker'])):
   if x['settled']<=t:
    cash+=5*x['won'];daily[day(x['settled'])]+=x['net'];ldaily[(day(x['settled']),x['lane'])]+=x['net'];closed.append(x)
   else:rest.append(x)
  active=rest
 for x in sorted(pool,key=lambda r:(r['entered'],r['lane']!=Z2,ASSETS.index(r['asset']),r['t'])):
  settle(x['entered'])
  if x['ticker'] in seen:continue
  reserve=4.75 if x['lane']==Z2 else 5.;risk=reserve+sum(y['reserve'] for y in active);d=day(x['entered']);reason=''
  if any(y['asset']==x['asset'] for y in active):reason='asset'
  elif (len(active)+1)*5>25:reason='contracts'
  elif risk>25+1e-8:reason='exposure'
  elif daily[d]-risk < -20-1e-8:reason='daily_loss_including_open_risk'
  elif cash<2 or cash<reserve:reason='cash'
  elif x['lane']==Z3:
   own=[y for y in active if y['lane']==Z3];ownrisk=5*(len(own)+1)
   if ownrisk>25:reason='premium_risk'
   elif ldaily[(d,Z3)]-ownrisk < -25-1e-8:reason='premium_daily_loss'
  if reason:blocks[reason]+=1;continue
  cash-=x['cost']+x['fee']+5*stress
  active.append(dict(x,net=x['net']-5*stress,reserve=reserve));seen.add(x['ticker']);maxrisk=max(maxrisk,risk);maxcount=max(maxcount,len(active)*5)
 settle(float('inf'))
 return sorted(closed,key=lambda x:(x['settled'],x['ticker'])),dict(blocks=blocks,final_cash=round(cash,4),max_reserved=maxrisk,max_contracts=maxcount)
def metrics(rows):
 net=peak=dd=0
 for r in sorted(rows,key=lambda r:(r['settled'],r['ticker'])):
  net+=r['net'];peak=max(peak,net);dd=max(dd,peak-net)
 waits=sorted(r['wait'] for r in rows)
 return dict(trades=len(rows),wins=sum(r['won'] for r in rows),losses=sum(1-r['won'] for r in rows),net=round(net,4),fees=round(sum(r['fee'] for r in rows),4),drawdown=round(dd,4),mean_entry_c=round(100*np.mean([r['entry'] for r in rows]),4) if rows else None,median_wait=round(float(np.median(waits)),3) if waits else None,p95_wait=round(float(np.quantile(waits,.95)),3) if waits else None)
def compare(base,new):
 b={r['ticker']:r for r in base};n={r['ticker']:r for r in new};matched=[k for k in b if k in n];added=[r for k,r in n.items() if k not in b];missed=[r for k,r in b.items() if k not in n]
 return dict(additional=metrics(added),missed=metrics(missed),net_trade_change=len(n)-len(b),net_pnl_change=round(sum(r['net'] for r in new)-sum(r['net'] for r in base),4),matched=len(matched),earlier=sum(n[k]['entered']<b[k]['entered'] for k in matched),side_changed=sum(n[k]['side']!=b[k]['side'] for k in matched),matched_net_change=round(sum(n[k]['net']-b[k]['net'] for k in matched),4),matched_average_price_improvement_c=round(np.mean([(b[k]['entry']-n[k]['entry'])*100 for k in matched]),4) if matched else None)
def bundles(sample):
 if sample=='dense':
  for p in sorted((P/'dense').glob('*.gz')):
   yield from pickle.load(gzip.open(p,'rb'))
 else:
  ms={m['ticker']:m for m in pickle.load(open(R/'kalshi_z_time_research_2026_10_03/features.pickle','rb')) if START<=m['start']<END}
  bb=defaultdict(list);src=R/'kalshi_z_time_research_2026_10_03/input_sources/0/books.jsonl.gz'
  for r in map(json.loads,gzip.open(src,'rt')):
   if r['ticker'] in ms:
    conv=lambda s:tuple((float(p),float(q)) for p,q in r[s] if float(q)>0)
    bb[r['ticker']].append((float(r['ts']),conv('yes'),conv('no')))
  origin,arrs=hist_cf()
  for ticker,m in sorted(ms.items()):yield dict(market=m,books=sorted(bb[ticker]),hist=(origin,arrs[m['asset']]))
def main():
 ap=argparse.ArgumentParser();ap.add_argument('sample',choices=['dense','sparse']);ap.add_argument('--quotes',default='cached',choices=['cached','stream']);a=ap.parse_args()
 modes=[(step,delay) for delay in (1,3) for step in (60,30,10,1)];pools={k:[] for k in modes};counts={k:Counter() for k in modes};coverage=Counter();starts=[];ends=[];gaps=[]
 for i,b in enumerate(bundles(a.sample)):
  m=b['market'];coverage[m['asset']]+=1;starts.append(max(m['close']-900,b.get('first',m['start'])));ends.append(m['close']);gaps.extend(y[0]-x[0] for x,y in zip(b['books'],b['books'][1:]))
  for key in modes:
   out,ct=opportunities(b,*key,quote_mode=a.quotes);pools[key].extend(out);counts[key].update(ct)
  if i%100==0:print(a.sample,'markets',i+1,flush=True)
 duration=(max(ends)-min(starts))/86400;results={};ledgers={}
 for delay in (1,3):
  for stress in (0,.01):
   base=None
   for step in (60,30,10,1):
    name=f'{step}s_delay{delay}_friction{stress:g}';rows,risk=simulate(pools[(step,delay)],stress)
    if base is None:base=rows
    results[name]=dict(**metrics(rows),risk=risk,signals=dict(counts[(step,delay)]),executable_markets=len({r['ticker'] for r in pools[(step,delay)]}),per_day=round(len(rows)/duration,3),by_asset={asset:dict(**metrics([r for r in rows if r['asset']==asset]),comparison=compare([r for r in base if r['asset']==asset],[r for r in rows if r['asset']==asset])) for asset in ASSETS},comparison=compare(base,rows),by_day={d:metrics([r for r in rows if day(r['entered'])==d]) for d in sorted({day(r['entered']) for r in rows})},first_half=metrics([r for r in rows if r['entered']<(min(starts)+max(ends))/2]),second_half=metrics([r for r in rows if r['entered']>=(min(starts)+max(ends))/2]))
    ledgers[name]=rows
 prefix=a.sample+('_stream' if a.quotes=='stream' else '')
 save(prefix+'_results.json',dict(quote_mode=a.quotes,coverage=dict(coverage),days=duration,first=min(starts),last=max(ends),book_gap_seconds={str(q):float(np.quantile(gaps,q)) for q in (.5,.9,.99,1)},results=results))
 with gzip.open(P/(prefix+'_ledgers.json.gz'),'wt') as f:json.dump(ledgers,f)
 with gzip.open(P/(prefix+'_opportunities.json.gz'),'wt') as f:json.dump({str(k):v for k,v in pools.items()},f)
 for k,v in results.items():print(k,v['trades'],v['wins'],v['losses'],v['net'],v['comparison']['net_trade_change'],flush=True)
if __name__=='__main__':main()
