from pathlib import Path
import shutil
P=Path(__file__).resolve().parent
p=P/'extension';p.mkdir(exist_ok=True)
old=P.parent/'kalshi_noncrypto15m_2026_10_07'
shutil.copytree(old/'source',p/'source',dirs_exist_ok=True)
s=(old/'pull.py').read_text().replace('ROOT = P.parent','ROOT = P.parent.parent')
a=s.index('ASSETS = (');b=s.index('\ndef ts',a)
s=s[:a]+'ASSETS = ("USDJPY","GBPUSD","AUDUSD","USDCAD","SILVER","PLATINUM","PALLADIUM","GOLD")\n'+s[b:]
s=s.replace('2026-10-08T00:00:00Z','2026-10-09T15:00:00Z').replace('+ .22','+ .8')
s=s.replace('for source in (legacy, prior, previous_z, path):','for source in (legacy, prior, previous_z, ROOT / "kalshi_noncrypto15m_2026_10_07/data" / path.name, path):')
s=s.replace("prior = ROOT / 'kalshi_commodity_transfer_2026_10_02/data/charts' / path.name","prior = ROOT / 'kalshi_noncrypto15m_2026_10_07/data/charts' / path.name\n    if not prior.exists():prior = ROOT / 'kalshi_commodity_transfer_2026_10_02/data/charts' / path.name")
(p/'pull.py').write_text(s)
s=(old/'replay.py').read_text().replace('ROOT = P.parent','ROOT = P.parent.parent').replace('range(10, 2, -1)','range(10, 0, -1)')
s=s.replace('> 20:', '> 25:').replace('> 19+1e-8:', '> 25+1e-8:').replace('< -10-1e-8:', '< -20-1e-8:').replace('own_count>20 or own_risk>20+1e-8','own_count>25 or own_risk>25+1e-8')
(p/'replay.py').write_text(s)
print('Prepared isolated extension',p)
