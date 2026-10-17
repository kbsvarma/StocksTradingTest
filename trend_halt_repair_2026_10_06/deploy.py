"""Deploy the tested paper-only repair through the existing service restart."""
import fcntl
import json
import os
import shutil
import signal
import sqlite3
import subprocess
import time
from pathlib import Path

import yaml

base = Path('/home/varma/trendbot/trend_bot')
audit = Path('/home/varma/trend_halt_repair_2026_10_06')


def run(*args):
    return subprocess.check_output(args, cwd=base, text=True).strip()


assert run('git', 'rev-parse', '--short', 'HEAD') == 'f800b18'
assert not run('git', 'status', '--porcelain', '--untracked-files=all')
assert run('git', 'rev-parse', '--short', 'codex/trend-protection-halt-recovery') == '72b13ea'
cfg = yaml.safe_load((base / 'config.yaml').read_text())
assert cfg['executor']['venue'] == 'ibkr_paper'
assert cfg['executor']['live_confirmed'] is False
preflight = json.loads((audit / 'preflight.json').read_text())
assert preflight['paper_accounts_only'] and preflight['would_clear_in_memory']
assert not preflight['position_diff']
assert preflight['protection'][0] == 'OK'
assert not preflight['pending_events'] and not preflight['unapplied_executions']
assert run('systemctl', 'show', 'trendbot.service', '-p', 'Restart', '--value') == 'always'
pid = int(run('systemctl', 'show', 'trendbot.service', '-p', 'MainPID', '--value'))
assert pid > 1 and Path(f'/proc/{pid}').stat().st_uid == os.getuid()
assert Path(f'/proc/{pid}/cwd').resolve() == base
assert b'bot.py' in Path(f'/proc/{pid}/cmdline').read_bytes()
assert json.loads((base / 'state/state.json').read_text())['halt_reason'] == 'ES_S position unprotected'
print(f'Stopping owned paper process {pid}; systemd will restart it.', flush=True)
os.kill(pid, signal.SIGINT)
deadline = time.monotonic() + 12
while Path(f'/proc/{pid}').exists() and time.monotonic() < deadline:
    time.sleep(.1)
assert not Path(f'/proc/{pid}').exists(), 'Process did not exit; source unchanged'
with (base / 'state/trading.lock').open('r+') as lock:
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    shutil.copy2(base / 'state/state.json', audit / 'state_before.json')
    source_db = sqlite3.connect(f'file:{base}/state/trendbot.db?mode=ro', uri=True)
    dest_db = sqlite3.connect(audit / 'trendbot_before.db')
    source_db.backup(dest_db)
    source_db.close()
    dest_db.close()
    print(run('git', 'merge', '--ff-only', 'codex/trend-protection-halt-recovery'))
    assert not run('git', 'status', '--porcelain', '--untracked-files=all')
print('Tested commit deployed; halt will be cleared by the bot after its own audit.', flush=True)
