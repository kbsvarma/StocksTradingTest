"""Copy public research inputs and frozen helpers into a portable archive."""
from pathlib import Path
import hashlib,json,shutil,importlib.metadata
P=Path(__file__).resolve().parent;R=P.parent
folders=['kalshi_restart_audit_2026_09_12','kalshi_parallel_design_2026_10_02','kalshi_execution_audit_2026_10_02','kalshi_btc_eth_quant_2026_10_02']
manifest={}
def copy(src,dst):
    dst.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(src,dst)
    sha=hashlib.sha256(src.read_bytes()).hexdigest();assert hashlib.sha256(dst.read_bytes()).hexdigest()==sha
    manifest[str(dst.relative_to(P))]=dict(source=str(src.relative_to(R)),sha256=sha)
for i,name in enumerate(folders):
    for a in ['BTC','ETH','SOL','XRP']:
        for kind in ['markets','candles','spot']:
            copy(R/name/'fresh_data'/f'{kind}_{a}.jsonl',P/'input_sources'/str(i)/f'{kind}_{a}.jsonl')
copy(R/folders[0]/'fresh_data/books.jsonl.gz',P/'input_sources/0/books.jsonl.gz')
copy(R/folders[-1]/'features.pickle',P/'prior_features.pickle')
for name in ['research.py','strategy.py','portfolio.py']:
    copy(R/folders[1]/name,P/'support'/name)
release=R/'kalshi_execution_improvements_2026_10_03/candidate'
for name in ['engine.py','lanes.py','sizing.py','staged_hashes.json']:
    copy(release/name,P/'production_snapshot'/name)
(P/'input_manifest.json').write_text(json.dumps(manifest,indent=2))
(P/'requirements.txt').write_text(''.join(name+'=='+importlib.metadata.version(name)+'\n' for name in ['numpy','numba','matplotlib']))
print('Portable inputs copied and verified',len(manifest))
