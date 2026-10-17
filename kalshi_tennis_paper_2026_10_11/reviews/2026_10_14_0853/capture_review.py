"""Capture read-only paper receipts for the first 20-settlement review."""
from pathlib import Path
import datetime as dt
import hashlib
import json
import shutil
import sqlite3
import subprocess
import time

P = Path(__file__).resolve().parent
ROOT = P.parent.parent
assert ROOT == Path('/home/varma/kalshi_tennis_paper_2026_10_11')
assert not (P / 'paper_snapshot.sqlite').exists(), 'Preserve the first captured snapshot'
manifest = json.loads((ROOT / 'deployment_manifest.json').read_text())
hashes = {}
for name in list(manifest) + ['deployment_manifest.json', 'status.json', 'review_state.json']:
    shutil.copy2(ROOT / name, P / name)
    hashes[name] = hashlib.sha256((P / name).read_bytes()).hexdigest()
shutil.copy2(P / 'review_state.json', P / 'prior_review_state.json')
src = sqlite3.connect((ROOT / 'paper.sqlite').as_uri() + '?mode=ro', uri=True)
dst = sqlite3.connect(P / 'paper_snapshot.sqlite')
src.backup(dst, pages=256, sleep=0.05)
dst.close()
src.close()
now = time.time()
def run(args):
    return subprocess.check_output(args, text=True, stderr=subprocess.STDOUT, timeout=25)
capture = {
    'captured_ts': now,
    'captured_utc': dt.datetime.fromtimestamp(now, dt.timezone.utc).isoformat(),
    'service': run(['systemctl','--user','show','kalshi-tennis90-paper.service','-p','ActiveState','-p','SubState','-p','MainPID','-p','NRestarts','-p','ExecMainStartTimestamp','-p','ExecMainStatus']),
    'clock': run(['timedatectl','show','-p','NTPSynchronized','-p','TimeUSec']),
    'hashes': hashes,
    'deployment_mismatches': {k: {'expected':v,'actual':hashes[k]} for k,v in manifest.items() if hashes[k] != v},
    'snapshot_sha256': hashlib.sha256((P / 'paper_snapshot.sqlite').read_bytes()).hexdigest(),
}
(P / 'capture.json').write_text(json.dumps(capture, indent=2)+'\n')
previous = json.loads((P / 'prior_review_state.json').read_text())
(P / 'journal_since_previous.txt').write_text(run(['journalctl','--user','-u','kalshi-tennis90-paper.service','--since',previous['last_checked_utc'],'--no-pager','-n','300']))
print(json.dumps(capture, indent=2))
