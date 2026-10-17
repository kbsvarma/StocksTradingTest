import pull_public as p
from pathlib import Path
p.ROOT=Path(__file__).resolve().parent/'data';p.ROOT.mkdir(exist_ok=True)
p.START=p.ts('2026-09-11T00:00:00Z');p.END=p.ts('2026-09-25T00:00:00Z')
for a in ['GOLD','WTI','SILVER']:p.kalshi(a)
print('COMPLETE',flush=True)
