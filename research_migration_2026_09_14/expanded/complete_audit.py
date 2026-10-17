from pathlib import Path
import collections
import datetime
import json
import os
import shutil
import subprocess
import time

P = Path(__file__).resolve().parent
ROOT = P.parents[1]
BASE = ROOT.parent
HOST = 'varmak@192.168.3.36'
REMOTE = '/home/varmak/research_data'
while not all((P / name).exists() for name in ['GIT_CLEANUP_COMPLETE', 'PROJECTS_MIGRATION_COMPLETE']):
    time.sleep(10)

print('Reconciling all deletion receipts and final repository state.', flush=True)
first = json.loads((P.parent / 'cleanup_summary.json').read_text())
git = json.loads((P / 'git_cleanup_summary.json').read_text())
projects = json.loads((P / 'projects_cleanup_summary.json').read_text())
verified = {}
with (P / 'projects_verified.jsonl').open() as f:
    for line in f:
        row = json.loads(line)
        verified[row['path']] = (row['sha256'], row['size'])
missing_receipts = []
recreated = []
removed_count = removed_bytes = 0
with (P / 'projects_removed.jsonl').open() as f:
    for line in f:
        row = json.loads(line)
        if verified.get(row['path']) != (row['sha256'], row['size']):
            missing_receipts.append(row['path'])
        if (BASE / row['path']).exists():
            recreated.append(row['path'])
        removed_count += 1
        removed_bytes += row['size']
assert not missing_receipts, missing_receipts[:5]
assert (removed_count, removed_bytes) == (projects['removed_files'], projects['removed_bytes'])
empty_cache_dirs_removed = 0
cache_root = BASE / 'KalshiWeatherTest/data/raw'
for directory, _, _ in os.walk(cache_root, topdown=False, followlinks=False):
    path = Path(directory)
    if path == cache_root or path.is_symlink():
        continue
    try:
        path.rmdir()
        empty_cache_dirs_removed += 1
    except OSError:
        pass
skipped = []
if (P / 'projects_skipped.jsonl').exists():
    skipped = [json.loads(line) for line in (P / 'projects_skipped.jsonl').read_text().splitlines()]
count = subprocess.run(['git', 'count-objects', '-v'], cwd=ROOT, capture_output=True, text=True, check=True)
(P / 'git_count_after.txt').write_text(count.stdout + count.stderr)
assert 'garbage: 0' in count.stdout
git_size = int(subprocess.check_output(['du', '-sk', str(ROOT / '.git')], text=True).split()[0]) * 1024
service_cmd = ('systemctl is-active trendbot.service trendbot-dashboard.service; '
               'systemctl --user is-active advisor-terminal.service advisor-quoted.service '
               'advisor-exitwatch.service kalshi-temp-bot.service kalshi-rain-bot.service '
               'kalshi-sports-bot.service')
services = subprocess.check_output(['ssh', '-o', 'BatchMode=yes', HOST, service_cmd], text=True).splitlines()
assert len(services) == 8 and all(s == 'active' for s in services), services
total = first['removed_bytes'] + git['removed_bytes'] + projects['removed_bytes']
summary = dict(at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
               initial_research=first, git_cleanup=git, expanded_research=projects,
               total_removed_bytes=total, total_removed_GiB=total / 1024**3,
               local_free_bytes=shutil.disk_usage(ROOT).free, git_remaining_bytes=git_size,
               skipped_transfer_files=skipped, recreated_local_paths=recreated,
               empty_cache_dirs_removed=empty_cache_dirs_removed,
               removal_receipts='all matched verified SHA256 receipts',
               production_services=services,
               retained='Source, normal Git history, recent loose Git objects, tracked fixtures, application assets and runtime databases.')
(P / 'final_expanded_audit.json').write_text(json.dumps(summary, indent=2))
doc = ROOT / 'RESEARCH_DATA_REMOTE.md'
text = doc.read_text()
text += ('\n## Expanded project and Git cleanup\n\n'
         'Scanned all 10 projects under the local GitHub directory. Research data use '
         '`' + HOST + ':' + REMOTE + '/PROJECT/`. Run with '
         '`ssh ' + HOST + ' ' + REMOTE + '/research-run PROJECT relative/script.py`.\n\n'
         f'Expanded research offload removed {projects["removed_files"]:,} verified files '
         f'({projects["removed_bytes"] / 1024**3:.2f} GiB). Git cleanup removed '
         f'{git["removed_files"]} abandoned temporary pack files '
         f'({git["removed_bytes"] / 1024**3:.2f} GiB), after archiving and SHA256 verification. '
         f'Total logical bytes removed across all phases: {total / 1024**3:.2f} GiB.\n\n'
         'Git archives: `' + REMOTE + '/_git_archives/StocksTradingTest/2026-09-14/`. '
         'Duplicate temporary packs are stored once per SHA256; the manifest maps each original filename. '
         'Git connectivity passed and references/worktrees were unchanged. Normal history and recent '
         'loose objects remain local; no history rewriting or expiry of recent objects was performed.\n\n'
         'Expanded per-file verification/deletion receipts and final audit: `'
         + REMOTE + '/_migration_audits/2026-09-14-expanded/`. '
         'Runtime databases remain local because they contain application state. '
         'Remote sample reads passed for every project with offloaded research; this does not establish '
         'that every legacy script has all optional dependencies installed.\n')
doc.write_text(text)
audit = REMOTE + '/_migration_audits/2026-09-14-expanded/'
subprocess.run(['scp', '-q', str(P / 'final_expanded_audit.json'), str(P / 'git_count_after.txt'),
                str(P / 'git_cleanup_summary.json'), str(P / 'git_fsck_after.log'),
                HOST + ':' + audit], check=True)
subprocess.run(['scp', '-q', str(doc), HOST + ':' + REMOTE + '/StocksTradingTest/RESEARCH_DATA_REMOTE.md'], check=True)
(P / 'ALL_MIGRATIONS_COMPLETE').write_text(str(time.time()))
print(json.dumps({key: summary[key] for key in ['total_removed_GiB', 'local_free_bytes', 'git_remaining_bytes', 'skipped_transfer_files', 'recreated_local_paths']}), flush=True)
