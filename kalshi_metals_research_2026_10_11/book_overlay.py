"""Partial recorded-book execution replay and full-fill stress; never a depth-complete backtest."""
import gzip,json,pickle,copy,math
from collections import defaultdict,Counter
import study as s
P=s.P;markets=pickle.loads((P/'features.pickle').read_bytes());pred=pickle.loads((P/'predictions.pickle').read_bytes())
with gzip.open(P/'actual_metal_events.json.gz','rt') as f:events=json.load(f)
books=defaultdict(list)
for r in events:
 if r.get('side') not in ('yes','no'):continue
 for tk,lk in (('book_observed_ts','book_levels'),('execution_book_ts','execution_book_levels')):
  if tk in r and lk in r and 0<=r[tk]-r['decision_ts']<=25:
   books[(r['ticker'],r['decision_ts'],1 if r['side']=='yes' else -1)].append(dict(ts=r[tk],levels=r[lk],source='live_execution_event'))
observer=P.parent/'kalshi_execution_retry_2026_10_02/observer_state/observations.jsonl'
# Observer side is recovered from the same saved signal; never infer it from the result.
sides={(r.get('ticker'),r.get('decision_ts'),r.get('lane')):1 if r.get('side')=='yes' else -1 for r in events if r.get('side') in ('yes','no')}
if observer.exists():
 for line in observer.open():
  r=json.loads(line);key=(r.get('ticker'),r.get('decision_ts'),r.get('lane'));side=sides.get(key)
  if side and r.get('levels') is not None and 0<=r.get('observed_ts',0)-r['decision_ts']<=25:books[(r['ticker'],r['decision_ts'],side)].append(dict(ts=r['observed_ts'],levels=r['levels'],source='independent_observer'))
books={k:sorted({(v['ts'],json.dumps(v['levels'])):v for v in vv}.values(),key=lambda v:v['ts']) for k,vv in books.items()}
s.save('book_evidence_summary.json',dict(decisions=len(books),snapshots=sum(map(len,books.values())),asset='COPPER',known_filled_markets=15,selection='All saved decisions for the 15 actual Copper markets, not a complete universe of other untraded markets'))

def recorded_quote(ticker,T,side,floor,limit):
 for book in books.get((ticker,T,side),[]):
  qty=0;cost=0;bad=False
  for px,size in sorted((1-float(p),float(q)) for p,q in book['levels']):
   if size<=0:continue
   if px<floor-1e-8:bad=True
   if px>limit+1e-8:break
   take=min(5-qty,size);qty+=take;cost+=px*take
   if qty>=5-1e-8:break
  if qty>=5-1e-8 and not bad:return cost/5,book['ts'],book['source']
 return None

def qualified(m,f,c):
 if c['family']!='cushion':return []
 if not c['lo']<=f['rem']<=c['hi'] or f['ask']-f['bid']>.02000001 or not s.filter_pass(f,c):return []
 return [(lane,floor,absolute) for lane,z,floor,ceil,absolute in (('original',c['z'],c['floor'],.95,.939),('premium',c['z']+1,.93,.98,.975)) if floor-1e-8<=f['ask']<ceil-1e-8 and f[c['vol']]>=z]

def supplemented(c,mode):
 rows,_=s.opportunities(markets,c,pred,'base');mapping={(r['ticker'],r['t'],r['lane']):r for r in rows};added=[];replaced=[]
 for m in markets:
  for f in m['features']:
   for lane,floor,absolute in qualified(m,f,c):
    cap=s.cap(f['ask'],f['side'],absolute,json.dumps(m['ranges'],sort_keys=True));known=recorded_quote(m['ticker'],f['t'],f['side'],floor,cap)
    if mode=='observed' and known is None:continue
    if mode=='all_signal_cap':known=(cap,f['t']+3,'assumed_full_fill_at_cap')
    if known is None:continue
    price,stamp,source=known;entry=min(cap,max(f['ask'],price));paid=s.fee(entry,5,m['multiplier']);won=int(m['result']==int(f['side']==1));key=(m['ticker'],f['t'],lane)
    row=dict(ticker=m['ticker'],asset=m['asset'],config=c['id'],lane=lane,t=f['t'],entered=stamp,close=m['close'],settled=m['settled'],side=f['side'],won=won,quantity=5,entry=entry,cap=cap,ask=f['ask'],bookask=price,floor=floor,fee=paid,z=f['z'],rem=f['rem'],net_before_friction=5*(won-entry)-paid,pwin=None,reserve=5*(1 if lane=='premium' else .95),hour=f['hour'],zrob=f['zrob'],zh=f['zh'],volratio=f['volratio'],jump=f['jump'],flat=f['flat_minutes'],zero_fraction=f['zero_fraction'],source=source)
    (replaced if key in mapping else added).append(dict(ticker=m['ticker'],t=f['t'],lane=lane,won=won,source=source))
    mapping[key]=row
 return list(mapping.values()),dict(added=added,replaced=replaced)

frozen=json.loads((P/'choices_frozen.json').read_text())['choices'];ids=sorted({0,1,3,7,20})
results={};ledgers={}
for cid in ids:
 c=s.CONFIG[cid];results[str(cid)]={};ledgers[str(cid)]={}
 for mode in ('observed','all_signal_cap'):
  pool,evidence=supplemented(c,mode);results[str(cid)][mode]={'evidence':evidence};ledgers[str(cid)][mode]={}
  for a in s.ASSETS:
   results[str(cid)][mode][a]={};ledgers[str(cid)][mode][a]={}
   for period in s.PERIODS:
    results[str(cid)][mode][a][period]={};ledgers[str(cid)][mode][a][period]={}
    for scenario,stress in (('base',.01),('cost2',.02)):
     metric,ledger=s.evaluate(pool,a,period,stress);results[str(cid)][mode][a][period][scenario]=metric;ledgers[str(cid)][mode][a][period][scenario]=ledger
s.save('book_overlay_results.json',results)
with gzip.open(P/'book_overlay_ledgers.json.gz','wt') as f:json.dump(ledgers,f)
for a in s.ASSETS:
 cid=str(frozen[a]['selected']);print(a,s.CONFIG[int(cid)]['name'])
 for mode in ('observed','all_signal_cap'):
  print(mode,{p:{k:results[cid][mode][a][p]['base'][k] for k in ('trades','wins','losses','net','drawdown')} for p in ('pooled','confirmation','recent')},flush=True)
