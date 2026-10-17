"""Public GET-only crypto data. No private account or order client imports."""
import importlib.util,sys,json,time,requests,concurrent.futures as cf
from pathlib import Path
P=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('public_pull',P.parent/'kalshi_currency_review_2026_10_12/pull.py')
p=importlib.util.module_from_spec(spec);spec.loader.exec_module(p)
p.P=P;p.DATA=P/'recent_data';p.DATA.mkdir(exist_ok=True)
p.START=p.ts('2026-10-03T00:00:00Z');p.END=p.ts('2026-10-14T15:30:01Z')
p.ASSETS=('BTC','ETH','SOL','XRP')
def spot(a):
 path=p.DATA/f'spot_{a}.jsonl'
 done={json.loads(l)['start'] for l in path.read_text().splitlines()} if path.exists() else set()
 session=requests.Session()
 for t in range(p.START-7200,p.END,290*60):
  if t in done:continue
  for trial in range(5):
   try:
    reply=session.get(f'https://api.exchange.coinbase.com/products/{a}-USD/candles',params=dict(granularity=60,start=t,end=min(t+290*60,p.END)),timeout=20)
    reply.raise_for_status();raw=reply.json();assert isinstance(raw,list)
    with path.open('a') as f:f.write(json.dumps(dict(start=t,end=min(t+290*60,p.END),received_ts=time.time(),url=reply.url,rows=raw))+'\n')
    time.sleep(.25);break
   except Exception:
    if trial==4:raise
    time.sleep(2**trial)
 print(a,'spot complete',flush=True)
def kalshi(a):
 rows=p.markets(a);p.candles(a,rows);print(a,'market inputs complete',flush=True)
if __name__=='__main__':
 with cf.ThreadPoolExecutor(max_workers=2) as pool:
  for a in p.ASSETS:
   fs=[pool.submit(spot,a),pool.submit(kalshi,a)]
   for f in fs:f.result()
 p.write(P/'recent_pull_receipt.json',dict(start=p.START,end=p.END,finished=time.time(),assets=p.ASSETS,public_get_only=True))
