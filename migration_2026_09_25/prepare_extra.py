from pathlib import Path
import json,hashlib,shutil,subprocess
ROOT=Path('/home/varma/migration_2026_09_25');B=ROOT/'migration-extra-20260925'
m=json.loads((B/'manifest.json').read_text())
for r in m['files']:
 if 'path' in r:assert hashlib.sha256((B/r['path']).read_bytes()).hexdigest()==r['sha256'],r['path']
print('Extra snapshot hashes verified',flush=True)
for name in ['advisor','kalshi_crypto']:
 base=ROOT/('test-'+name);base.mkdir(exist_ok=True)
 dst=base/('advisor' if name=='advisor' else 'bot')
 if not dst.exists():shutil.copytree(B/name,dst)
 if name=='kalshi_crypto':(dst/'state/HALT').touch()
 py=base/'.venv/bin/python'
 if not py.exists():subprocess.run(['/home/varma/.local/bin/python3.12','-m','venv',str(base/'.venv')],check=True)
 log=ROOT/(name+'-dependencies.log')
 with log.open('w') as out:
  subprocess.run([str(py),'-m','pip','install','--no-deps','-r',str(B/(name+'-runtime.lock'))],stdout=out,stderr=subprocess.STDOUT,check=True)
  subprocess.run([str(py),'-m','pip','check'],stdout=out,stderr=subprocess.STDOUT,check=True)
 print(name,'dependencies installed and checked',flush=True)
 if name=='kalshi_crypto':
  with (ROOT/'kalshi-tests.log').open('w') as out:subprocess.run([str(py),'-m','unittest','discover','-q'],cwd=dst,stdout=out,stderr=subprocess.STDOUT,check=True)
  print('Kalshi offline tests passed',flush=True)
print('Extra environments ready; no services installed or started',flush=True)
