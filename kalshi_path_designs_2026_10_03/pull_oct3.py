"""Public GET only. Freeze candidate before a newly downloaded October3 check."""
from pathlib import Path
import concurrent.futures as cf,hashlib,json,sys,time
P=Path(__file__).resolve().parent
sys.path.insert(0,str(P.parent/'kalshi_parallel_design_2026_10_02'));import pull
pull.ROOT=P/'oct3_public';pull.ROOT.mkdir(exist_ok=True)
receipt=P/'oct3_frozen.json'
if receipt.exists():f=json.loads(receipt.read_text())
else:
    f=dict(ts=time.time(),start=pull.ts('2026-10-03T00:00:00Z'),end=int(time.time())-180,
           variants=['baseline','impulse801_size5','impulse801_starter1_then4','impulse801_starter2_then3'],
           source_sha256={n:hashlib.sha256((P/n).read_bytes()).hexdigest() for n in ['designs.py','impulse.py','staged.py']},
           caveat='Newly downloaded after freezing these variants; not a long forward test. October3 was seen in other operational work, but these designs were not selected on these outcomes.')
    receipt.write_text(json.dumps(f,indent=2))
pull.START=f['start'];pull.END=f['end'];pull.ASSETS=['BTC','ETH','SOL','XRP']
with cf.ThreadPoolExecutor(max_workers=2) as pool:
    fut=[pool.submit(fn,a) for fn in [pull.kalshi,pull.coinbase] for a in pull.ASSETS]
    for x in cf.as_completed(fut):x.result()
print('COMPLETE',f['end'],flush=True)
