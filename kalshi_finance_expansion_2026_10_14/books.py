"""Bounded public book audit only: no orders or ongoing collection."""
from common import *
import engine as e
SERIES=('KXAUDUSD15M','KXUSDCAD15M','KXUSDJPY15M','KXGBPUSD15M','KXDJI','KXINXU','KXNASDAQ100U','KX10YRRATEH')
def fresh_markets(series):
 rows=[];cursor='';seen=set()
 while True:
  x=get('/markets',dict(series_ticker=series,status='open',limit=1000,**({'cursor':cursor} if cursor else {})),cache=False);rows.extend(x.get('markets',[]));cursor=x.get('cursor')
  if not cursor:return rows
  assert cursor not in seen;seen.add(cursor)
rows=[]
for series in SERIES:
 try:
  ms=fresh_markets(series)
  save(P/'books_fresh'/(series+'_markets.json'),ms)
  candidates=[]
  for m in ms:
   if ts(m['close_time'])<=time.time()+30:continue
   try:b=float(m['yes_bid_dollars']);a=float(m['yes_ask_dollars'])
   except (KeyError,ValueError,TypeError):continue
   if not 0<=b<=a<=1:continue
   side=1 if a+b>1 else -1;ask=a if side==1 else 1-b;bid=b if side==1 else 1-a
   if .85-1e-8<=ask<.98 and ask-bid<=.02000001:candidates.append((ts(m['close_time']),ask,m['ticker'],m,side))
  if not candidates and len(ms)==1 and ts(ms[0]['close_time'])>time.time()+30:candidates=[(ts(ms[0]['close_time']),0,ms[0]['ticker'],ms[0],None)]
  picks=sorted(candidates)[:3]
  for _,_,ticker,m,side in picks:
   observations=[]
   for repeat in range(2):
    before=time.time();book=get('/markets/'+ticker+'/orderbook',dict(depth=100),cache=False);after=time.time();observations.append(dict(start_ts=before,end_ts=after,before_expiry=after<ts(m['close_time']),response=book))
   row=dict(series=series,ticker=ticker,close=m['close_time'],snapshot_only_not_signal=True,observations=observations)
   save(P/'books_fresh'/(ticker+'.json'),row);rows.append(row)
  print(series,'open',len(ms),'price_spread_candidates',len(candidates),'sampled',len(picks),flush=True)
 except Exception as ex:rows.append(dict(series=series,error=str(ex)))
save(P/'book_samples_fresh.json',rows)
