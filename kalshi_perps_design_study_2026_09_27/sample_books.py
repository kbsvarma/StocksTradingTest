import sys,json,time,concurrent.futures,statistics
from pathlib import Path
P=Path(__file__).resolve().parent;sys.path.insert(0,str(P.parent/'kalshi_full_review_2026_09_26'));from census import get
assets=['BTC','ETH','LTC','GOLD','SILVER'];rows=[]
def one(a):
 t='KX'+a+'PERP';stamp=time.time();b=get('/margin/markets/'+t+'/orderbook',{'depth':30})['orderbook'];asks=sorted((float(p),float(q)) for p,q in b['asks']);bids=sorted(((float(p),float(q)) for p,q in b['bids']),reverse=True)
 def vwap(levels,count):
  remain=count;cost=0
  for p,q in levels:
   n=min(remain,q);cost+=p*n;remain-=n
   if remain<=0:return cost/count
  return None
 if not asks or not bids:return dict(asset=a,ts=stamp,missing=True)
 mid=(asks[0][0]+bids[0][0])/2;count=int(50/asks[0][0]);spread=(asks[0][0]-bids[0][0])/mid*10000;buy=vwap(asks,count);sell=vwap(bids,count)
 return dict(asset=a,ts=stamp,spread_bp=spread,count=count,depth_spread_bp=(buy-sell)/mid*10000 if buy and sell else None,book=b)
for i in range(12):
 with concurrent.futures.ThreadPoolExecutor(max_workers=5) as ex:rows.extend(ex.map(one,assets))
 time.sleep(2)
(P/'data/book_samples.json').write_text(json.dumps(rows));out=[]
for a in assets:
 r=[r for r in rows if r['asset']==a and not r.get('missing')];fee=.12 if a in ['GOLD','SILVER'] else 2
 out.append(dict(asset=a,samples=len(r),median_spread_bp=statistics.median(r['spread_bp'] for r in r),median_50dollar_spread_bp=statistics.median(r['depth_spread_bp'] for r in r if r['depth_spread_bp'] is not None),positive_maker_spread_samples=sum(r['spread_bp']>2*fee for r in r),elapsed_seconds=max(r['ts'] for r in r)-min(r['ts'] for r in r)))
(P/'book_sample_summary.json').write_text(json.dumps(out,indent=2));print(json.dumps(out,indent=2))
