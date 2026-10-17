"""CPU-only lookup timing on a copied historical ledger, no live DB or network."""
import json,sys,time,tempfile,statistics
from pathlib import Path
P=Path(__file__).resolve().parent;sys.path.insert(0,str(P/sys.argv[1]));from engine import Store,Engine
rows=json.loads((P/'evidence/before_orders.json').read_text())
with tempfile.TemporaryDirectory() as tmp:
 store=Store(Path(tmp)/'copy.sqlite');store.db.executemany('insert into orders values (?,?)',[(r['ticker'],json.dumps(r)) for r in rows]);store.db.commit();e=Engine(None,store,tmp,clock=lambda:1792001271)
 values=[]
 for _ in range(20):
  t=time.perf_counter()
  for lane in ('cushion_z2','premium_z3'):
   for asset in ('BTC','ETH','SOL','XRP','NATGAS','EURUSD','WTI','USDCAD'):e.entered_markets(lane,1792001220);e.retry_signal(asset,lane,1792001220)
  values.append((time.perf_counter()-t)*1000)
 store.db.close()
print(json.dumps(dict(version=sys.argv[1],record_count=len(rows),runs=len(values),entire_16_lane_ownership_retry_lookup_ms=dict(median=statistics.median(values),min=min(values),max=max(values)))))
