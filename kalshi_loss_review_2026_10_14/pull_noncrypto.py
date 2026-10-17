"""Public historical GETs for active noncrypto lanes, isolated on this Mac."""
import importlib.util,concurrent.futures as cf,shutil,json,time
from pathlib import Path
P=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('noncrypto_pull',P.parent/'kalshi_currency_review_2026_10_12/pull.py')
p=importlib.util.module_from_spec(spec);spec.loader.exec_module(p)
p.P=P;p.DATA=P/'noncrypto_data';p.DATA.mkdir(exist_ok=True)
p.START=p.ts('2026-10-08T04:00:00Z');p.END=p.ts('2026-10-14T15:30:01Z')
p.ASSETS=('NATGAS','WTI','EURUSD')
# Reuse archived responses without silently replacing them. New responses and
# their receipt times remain separate in this experiment's data directory.
(p.DATA/'charts').mkdir(exist_ok=True)
for source in [P.parent/'kalshi_currency_review_2026_10_12/data',P.parent/'kalshi_market_expansion_2026_10_09/extension/data']:
 for f in (source/'charts').glob('*.json.gz'):
  if any(f.name.startswith('KX'+a+'15M-') for a in p.ASSETS) and not (p.DATA/'charts'/f.name).exists():shutil.copy2(f,p.DATA/'charts'/f.name)
 for a in p.ASSETS:
  f=source/f'candles_{a}.jsonl'
  if f.exists() and not (p.DATA/f.name).exists():shutil.copy2(f,p.DATA/f.name)
if __name__=='__main__':p.main()
