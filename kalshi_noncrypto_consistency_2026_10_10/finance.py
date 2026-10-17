"""Full source/quote replays for frozen hourly threshold designs."""
from study import *
from decimal import Decimal,ROUND_FLOOR
import sys
START=ts('2026-08-01T04:00:00Z');END=ts('2026-10-10T04:00:00Z');SPLIT=ts('2026-09-05T04:00:00Z')
PROFILES=('late_z2_z3','early_z2','probability')
def legalcap(cap,m):
 v=Decimal(str(cap));best=Decimal('0')
 for r in m.get('price_ranges',[]):
  lo,hi,step=map(Decimal,(r['start'],r['end'],r['step']))
  if v>=lo:
   lim=min(v,hi);q=lo+((lim-lo)/step).to_integral_value(rounding=ROUND_FLOOR)*step
   best=max(best,q)
 return float(best) if best else math.floor(cap*100+1e-9)/100

def bar_series(det):
 if not det.get('timeseries'):return {},{}
 # Canonical minute points for 1m charts; exact terminal second for finer charts.
 vals={};stamps={};fine=any(int(x['t'])%60000 for x in det['timeseries'])
 for x in det['timeseries']:
  tm=int(x['t']);v=float(x['v'])
  if not math.isfinite(v) or v<=0:continue
  if fine:
   if tm%60000!=59000:continue
  elif tm%60000:continue
  key=tm//60000*60;vals[key]=v;stamps[key]=tm/1000
 return vals,stamps

def features(det,cl):
 bars,stamps=bar_series(det);out={}
 for rem in range(30,2,-1):
  t=cl-rem*60;keys=list(range(t-3660,t,60))
  if not all(k in bars for k in keys):continue
  prices=[bars[k] for k in keys];sig=statistics.pstdev(math.log(b/a) for a,b in zip(prices,prices[1:]))
  if sig<=0:continue
  out[rem]={'t':t,'rem':rem,'sigma':sig,'spot':prices[-1],'source_ts':stamps[keys[-1]],'first_source_ts':stamps[keys[0]]}
 return out,bars

def signals(items,det,cm,mult):
 cl=ts(items[0]['close_time']);fs,bars=features(det,cl);output=defaultdict(list);asset=det.get('asset',items[0]['event_ticker'].split('-')[0]);cmap={m['ticker']:{int(c['end_period_ts']):c for c in cm.get(m['ticker'],[])} for m in items};audit=[]
 for m in items:
  v=m.get('expiration_value');obs=bars.get(cl,bars.get(cl-60))
  if v and obs is not None:
   try:final=float(str(v).replace(',',''));audit.append({'event':m['event_ticker'],'ticker':m['ticker'],'chart_final':obs,'official':final,'difference':obs-final,'chart_exact_expiry':cl in bars})
   except ValueError:pass
 for profile in PROFILES:
  for rem in sorted(fs,reverse=True):
   if profile=='late_z2_z3' and not 3<=rem<=10:continue
   if profile=='early_z2' and not 15<=rem<=30:continue
   if profile=='probability' and rem not in (30,15,10,5):continue
   f=fs[rem];t=f['t'];choices=[]
   for m in items:
    if not m.get('floor_strike') or m.get('strike_type') not in ('greater','greater_or_equal'):continue
    strike=float(m['floor_strike']);cs=cmap[m['ticker']];base=quote(cs.get(t))
    if not base:continue
    if sum(float(c.get('volume_fp') or 0) for tt,c in cs.items() if t-600<tt<=t)<5:continue
    fav='yes' if sum(base)>1 else 'no'
    for side in (('yes','no') if profile=='probability' else (fav,)):
     b,a=quote(cs.get(t),side)
     if a-b>.02000001:continue
     sign=1 if side=='yes' else -1;z=sign*(f['spot']/strike-1)/(f['sigma']*math.sqrt(rem));p=.5*(1+math.erf(z/(1.25*math.sqrt(2))));edge=p-a-fee(a,mult)/5-.01
     priority=0;maxcap=.939
     if profile=='probability':good=.60<=a<.95 and edge>=.03;maxcap=.95
     elif profile=='early_z2':good=.85<=a<.95 and z>=2
     else:
      if .85<=a<.95 and z>=2:good=True
      elif .93<=a<.98 and z>=3:good=True;priority=1;maxcap=.975
      else:good=False
     if not good:continue
     cap=legalcap(min(a+.01,maxcap),m)
     if cap<a-1e-8:continue
     choices.append(dict(kind='finance',asset=asset,series=m['event_ticker'].split('-')[0],event=m['event_ticker'],ticker=m['ticker'],side=side,profile=profile,priority=priority,t=t,ask=a,bid=b,close=cl,cap=cap,z=z,probability=p,edge=edge,strike=strike,**{k:v for k,v in f.items() if k!='t'},_cs=cs,_market=m,_mult=mult))
   if choices:
    # Selection uses decision information, never future execution prices/outcome.
    key=(lambda x:(-x['edge'],x['ask'],x['ticker'],x['side'])) if profile=='probability' else (lambda x:(x['priority'],x['ask'],-x['z'],x['ticker']))
    output[profile].append(min(choices,key=key))
 return output,{'source_feature_minutes':len(fs),'audit':audit}

def main():
 allsignals=defaultdict(list);coverage={};audits=[]
 for d in sorted((P/'financial').iterdir()):
  if not (d/'receipt.json').exists() and '--partial' not in sys.argv:continue
  ms=read(d/'markets.json');events=defaultdict(list);ct=Counter();meta=read(d/'series.json')
  for m in ms:events[m['event_ticker']].append(m)
  for et,items in sorted(events.items()):
   ct['events']+=1
   if d.name in ('KXGOLDH','KXSILVERH','KXWTIH','KXPALLADIUMH') and datetime.fromtimestamp(ts(items[0]['close_time']),ET).hour!=16:ct['outside_fixed_session']+=1;continue
   if not any(m.get('strike_type') in ('greater','greater_or_equal') and m.get('floor_strike') is not None and float(m.get('volume_fp') or 0)>=5 for m in items):ct['no_five_volume_threshold']+=1;continue
   ct['requested_events']+=1;cp=d/(et+'_chart.json.gz');qp=d/(et+'_candles.json.gz')
   if not cp.exists() or not qp.exists():ct['missing_inputs']+=1;continue
   try:det=read(cp)['live_data']['details']
   except KeyError:ct['missing_details']+=1;continue
   cm=read(qp);xs,qa=signals(items,det,cm,float(meta['fee_multiplier']));audits+=qa.pop('audit');ct.update(qa)
   ct['evaluated_events']+=1
   if not qa['source_feature_minutes']:ct['no_complete_source_event']+=1
   for prof,ss in xs.items():allsignals[prof]+=ss;ct[prof+'_signal_minutes']+=len(ss)
  coverage[d.name]=dict(ct);print('BUILT',d.name,dict(ct),flush=True)
 results={};ledgers={};execution={}
 for prof in PROFILES:
  for label,stress,mode in SCENARIOS:
   rows=[y for x in allsignals[prof] if (y:=execute(x,mode)) is not None];execution[prof+'/'+label]={'signal_minutes':len(allsignals[prof]),'execution_eligible':len(rows)}
   m=record(results,ledgers,prof+'/'+label,rows,stress,True,SPLIT)
   for asset in sorted({x['asset'] for x in allsignals[prof]}):record(results,ledgers,asset+'/'+prof+'/'+label,[x for x in rows if x['asset']==asset],stress,True,SPLIT)
   print('RESULT',prof,label,{k:v for k,v in m.items() if k in ('trades','wins','losses','net','drawdown','weekly')},flush=True)
 prefix='partial_' if '--partial' in sys.argv else ''
 save(P/(prefix+'financial_results.json'),results);save(P/(prefix+'financial_ledgers.json.gz'),ledgers);save(P/(prefix+'financial_coverage.json'),coverage);save(P/(prefix+'financial_source_audit.json.gz'),audits);save(P/(prefix+'financial_execution.json'),execution)
if __name__=='__main__':main()
