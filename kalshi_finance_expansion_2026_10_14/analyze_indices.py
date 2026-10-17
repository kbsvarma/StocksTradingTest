"""Four frozen index mechanisms, selecting strikes before future quote inspection."""
from common import *
import engine as e
import math,statistics,pickle,sys
OLDER="--older" in sys.argv
PREFIX="older_" if OLDER else ""
from collections import defaultdict,Counter
PROFILES=('strict_z2_z3','eur_curve','probability_5m','probability_15m')
SCENARIOS=(('quote1c','open',.01),('quote2c','open',.02),('cap1c','cap',.01),('delay60_1c','close',.01))
ASSETS={'KXDJI':'DJI','KXINXU':'INX','KXINX':'INX','KXINXDUD':'INX','KXNASDAQ100U':'NDQ','KXNASDAQ100':'NDQ','KXNASDAQDUD':'NDQ'}
def quote(c,phase='close'):
 try:b=float(c['yes_bid'][phase+'_dollars']);a=float(c['yes_ask'][phase+'_dollars'])
 except (TypeError,KeyError,ValueError):return None
 return (b,a) if math.isfinite(a+b) and 0<=b<=a<=1 else None
def source(det,cl):
 values={};stamps={};rr=det.get('timeseries',[]);fine=any(int(x['t'])%60000 for x in rr)
 for r in rr:
  t=int(r['t']);v=float(r['v'])
  if not math.isfinite(v) or v<=0 or (fine and t%60000!=59000) or (not fine and t%60000):continue
  values[t//60000*60]=v;stamps[t//60000*60]=t/1000
 out={}
 for rem in range(15,0,-1):
  t=cl-rem*60;keys=range(t-3660,t,60)
  if any(k not in values for k in keys):continue
  pp=[values[k] for k in keys];sig=statistics.pstdev(math.log(b/a) for a,b in zip(pp,pp[1:]))
  if sig>0:out[t]=dict(t=t,rem=rem,spot=pp[-1],sigma=sig,source_ts=stamps[t-60],source_first_ts=stamps[t-3660])
 return out,values
cdf=lambda x:.5*(1+math.erf(x/math.sqrt(2)))
def model(m,f):
 kind=m.get('strike_type');lo=m.get('floor_strike');hi=m.get('cap_strike');spot=f['spot'];sd=f['sigma']*math.sqrt(f['rem'])
 if kind in ('greater','greater_or_equal') and lo is not None and lo>0:
  zy=(spot/lo-1)/sd;p=cdf(math.log(spot/lo)/(1.25*sd));return p,zy,-zy
 if kind in ('less','less_or_equal') and hi is not None and hi>0:
  zy=(1-spot/hi)/sd;p=cdf(math.log(hi/spot)/(1.25*sd));return p,zy,-zy
 if kind=='between' and lo is not None and hi is not None and 0<lo<hi:
  zy=min(spot/lo-1,1-spot/hi)/sd;zn=(1-spot/lo)/sd if spot<lo else ((spot/hi-1)/sd if spot>hi else -999)
  p=cdf(math.log(hi/spot)/(1.25*sd))-cdf(math.log(lo/spot)/(1.25*sd));return p,zy,zn
 return None

def main():
 candidates={p:defaultdict(list) for p in PROFILES};coverage={};audits=[]
 for s,asset in ASSETS.items():
  if OLDER and s not in ('KXDJI','KXINXU','KXNASDAQ100U'):continue
  d=(P.parent/'kalshi_noncrypto_consistency_2026_10_10/financial'/s) if OLDER else P/'indices'/s
  if not (d/'receipt.json').exists():raise ValueError('incomplete '+s)
  ms=read(d/'markets.json');meta=read(d/'series.json');assert meta['fee_type']=='quadratic'
  events=defaultdict(list);ct=Counter()
  for m in ms:
   if OLDER and ts(m['close_time'])>=START:continue
   events[m['event_ticker']].append(m)
  for ev,items in sorted(events.items()):
   ct['events']+=1;cp=d/(ev+'_chart.json.gz');qp=d/(ev+'_candles.json.gz');cl=ts(items[0]['close_time'])
   if not cp.exists() or not qp.exists():ct['missing_chart_or_quotes']+=1;continue
   det=read(cp).get('live_data',{}).get('details',{})
   if det.get('asset')!=asset:ct['asset_mismatch']+=1;continue
   fs,bars=source(det,cl);ct['source_minutes']+=len(fs)
   if not fs:ct['no_complete_source']+=1;continue
   cm={k:{int(x['end_period_ts']):x for x in v} for k,v in read(qp).items()};ct['evaluated_events']+=1
   final=items[0].get('expiration_value');obs=bars.get(cl,bars.get(cl-60))
   if final and obs is not None:
    try:audits.append(dict(event=ev,chart=obs,official=float(final.replace(',','')),difference=obs-float(final.replace(',',''))))
    except (ValueError,AttributeError):pass
   for t,f in fs.items():
    for m in items:
     if ts(m['open_time'])>t or m['result'] not in ('yes','no'):continue
     cs=cm.get(m['ticker'],{});q=quote(cs.get(t))
     if not q:continue
     if sum(float(cs.get(t-i*60,{}).get('volume_fp') or 0) for i in range(5))<5:continue
     if q[1]-q[0]>.02000001:continue
     vals=model(m,f)
     if vals is None:continue
     p,zy,zn=vals;mid=sum(q)/2;p=.5*p+.5*mid;fav=1 if mid>.5 else -1
     for profile in PROFILES:
      value=profile.startswith('probability')
      if value and f['rem']!=int(profile.split('_')[-1][:-1]):continue
      if not value and not (1 if profile=='eur_curve' else 3)<=f['rem']<=10:continue
      for side in ((1,-1) if value else (fav,)):
       ask=q[1] if side==1 else 1-q[0];bid=q[0] if side==1 else 1-q[1];z=zy if side==1 else zn;pwin=p if side==1 else 1-p;edge=pwin-ask-e.fee(ask,meta['fee_multiplier'])/5-.01;options=[]
       if value:
        if .5-1e-8<=ask<.95-1e-8 and edge>=.03:options=[('value',0,.5,.95)]
       else:
        for lane,priority,base,floor,ceiling,abs_cap in [('original',0,2,.85,.95,.939),('premium',1,3,.93,.98,.975)]:
         threshold=max(.5,base-.25+.5*math.log2(f['rem']/5)) if profile=='eur_curve' else base
         if z>=threshold and floor-1e-8<=ask<ceiling-1e-8:options.append((lane,priority,floor,abs_cap))
       for lane,priority,floor,abs_cap in options:
        cap=e.cap(ask,side,abs_cap,m['price_ranges'])
        if cap<ask-1e-8:continue
        r=dict(ticker=m['ticker'],asset=asset,series=s,event=ev,profile=profile,lane=lane,priority=priority,side=side,ask=ask,bid=bid,cap=cap,floor=floor,close=cl,settled=ts(m['settlement_ts']),won=int(m['result']==('yes' if side==1 else 'no')),multiplier=float(meta['fee_multiplier']),z=z,pwin=pwin,edge=edge,quantity=5,reserve=4.75 if lane=='original' else 5,**f,_subseq=cs.get(t+60))
        candidates[profile][asset,cl,t].append(r);ct[profile+'_qualified_strike_minutes']+=1
  coverage[s]=dict(ct);print('BUILT',s,dict(ct),flush=True)
 pools={};results={};ledgers={};counts={}
 for prof in PROFILES:
  selected=[]
  for key,rr in candidates[prof].items():
   rank=(lambda x:(-x['edge'],x['ask'],x['ticker'],x['side'])) if prof.startswith('probability') else (lambda x:(x['priority'],x['ask'],-x['z'],x['ticker']))
   selected.append(min(rr,key=rank))
  counts[prof]=dict(selected_signal_minutes=len(selected),qualified_expiries=len({(r['asset'],r['close']) for r in selected}))
  pools[prof]={};results[prof]={};ledgers[prof]={}
  for label,mode,stress in SCENARIOS:
   rows=[];cnt=Counter()
   for r in selected:
    q=quote(r['_subseq'],'close' if mode=='close' else 'open');enter=r['t']+(60 if mode=='close' else 3)
    if enter>=r['close']:cnt['expired']+=1;continue
    if not q:cnt['missing_quote']+=1;continue
    ask=q[1] if r['side']==1 else 1-q[0]
    if not r['floor']-1e-8<=ask<=r['cap']+1e-8:cnt['outside_cap_floor']+=1;continue
    px=r['cap'] if mode=='cap' else max(r['ask'],ask)
    rows.append(dict(**{k:v for k,v in r.items() if not k.startswith('_')},entered=enter,entry=px,fee=e.fee(px,r['multiplier'])))
   pools[prof][label]=rows;results[prof][label]={};ledgers[prof][label]={};counts[prof][label]=dict(execution_eligible_minutes=len(rows),failures=dict(cnt))
   for asset in ('ALL','DJI','INX','NDQ'):
    rr=[r for r in rows if asset=='ALL' or r['asset']==asset];ll,diag=e.portfolio(rr,stress);ledgers[prof][label][asset]=ll
    results[prof][label][asset]=dict(**e.metrics(ll),portfolio=diag,prior=e.metrics([r for r in ll if r['close']<EXT]),extension=e.metrics([r for r in ll if r['close']>=EXT]),early=e.metrics([r for r in ll if r['close']<ts('2026-10-01T04:00:00Z')]),late=e.metrics([r for r in ll if r['close']>=ts('2026-10-01T04:00:00Z')]))
   print('RESULT',prof,label,{a:{k:results[prof][label][a][k] for k in ('trades','wins','losses','net','drawdown')} for a in ('ALL','DJI','INX','NDQ')},flush=True)
 save(P/(PREFIX+'indices_results.json'),results);save(P/(PREFIX+'indices_ledgers.json.gz'),ledgers);save(P/(PREFIX+'indices_pools.json.gz'),pools);save(P/(PREFIX+'indices_coverage.json'),coverage);save(P/(PREFIX+'indices_source_audit.json'),audits);save(P/(PREFIX+'indices_signal_counts.json'),counts)
if __name__=='__main__':main()
