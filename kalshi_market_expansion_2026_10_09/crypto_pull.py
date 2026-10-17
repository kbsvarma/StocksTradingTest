"""Five additional crypto assets, public GET-only, current two-lane rule inputs."""
import importlib.util,json,time,requests
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
P=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('input_pull',P/'extension/pull.py');p=importlib.util.module_from_spec(spec);spec.loader.exec_module(p)
p.DATA=P/'crypto/data';p.DATA.mkdir(parents=True,exist_ok=True)
p.ASSETS=('DOGE','HYPE','NEAR','ZEC','BNB');p.START=p.ts('2026-10-01T00:00:00Z');p.END=p.ts('2026-10-09T15:00:00Z')
def spot(a):
 path=p.DATA/('spot_'+a+'.jsonl')
 done={json.loads(l)['start'] for l in path.read_text().splitlines()} if path.exists() else set()
 for start in range(p.START-7200,p.END,290*60):
  if start in done:continue
  for attempt in range(4):
   with p.lock:
    delay=max(0,p.next_request-time.monotonic());p.next_request=max(p.next_request,time.monotonic())+.8
   time.sleep(delay)
   try:
    response=requests.get('https://api.exchange.coinbase.com/products/'+a+'-USD/candles',params=dict(granularity=60,start=start,end=min(start+290*60,p.END)),timeout=20);response.raise_for_status();raw=response.json();assert isinstance(raw,list)
    with path.open('a') as f:f.write(json.dumps(dict(start=start,received_ts=time.time(),url=response.url,rows=raw))+'\n')
    break
   except Exception:
    if attempt==3:raise
    time.sleep(2**attempt)
 print(a,'spot complete',flush=True)
def asset(a):
 rows=p.markets(a);p.candles(a,rows);spot(a)
if __name__=='__main__':
 with ThreadPoolExecutor(max_workers=3) as pool:list(pool.map(asset,p.ASSETS))
 (p.DATA/'receipt.json').write_text(json.dumps(dict(start=p.START,end=p.END,assets=p.ASSETS,public_get_only=True,finished=time.time()),indent=2))
