import pull_public as p
from pathlib import Path
from datetime import datetime,timezone
from concurrent.futures import ThreadPoolExecutor
p.ROOT=Path(__file__).resolve().parent/'data';p.ROOT.mkdir(exist_ok=True)
p.START=int(datetime(2026,9,1,tzinfo=timezone.utc).timestamp())
p.END=int(datetime(2026,9,15,tzinfo=timezone.utc).timestamp())
with ThreadPoolExecutor(max_workers=2) as ex:
 fs=[ex.submit(p.kalshi,'DOGE'),ex.submit(p.coinbase,'DOGE')]
 for f in fs:f.result()
print('PULL COMPLETE',flush=True)
