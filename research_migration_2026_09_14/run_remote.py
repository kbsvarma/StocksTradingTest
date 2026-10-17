"""Run research remotely; optionally forward the primary Databento key through encrypted stdin."""
import subprocess,sys,shlex,json
from pathlib import Path
SSH=['ssh','-i',str(Path.home()/'.ssh/id_ed25519_migration_192_168_3_136'),'-o','IdentitiesOnly=yes','-o','BatchMode=yes']
args=sys.argv[1:];with_key=bool(args and args[0]=='--databento')
if with_key:args=args[1:]
if not args:raise SystemExit('Usage: python run_remote.py [--databento] relative/script.py [arguments] (or -c CODE)')
runner='/home/varma/research-python';host='varma@192.168.3.136'
if not with_key:raise SystemExit(subprocess.call([*SSH,host,runner+' '+shlex.join(args)]))
key=subprocess.check_output(['security','find-generic-password','-a','StocksTradingTest','-s','com.stockstradingtest.databento','-w'],text=True).strip()
if not key:raise SystemExit('Primary Databento key unavailable')
code="import sys,json,os,subprocess;p=json.load(sys.stdin);e=os.environ.copy();e['DATABENTO_API_KEY']=p['key'];raise SystemExit(subprocess.call(p['argv'],env=e))"
payload=json.dumps(dict(key=key,argv=[runner,*args])).encode()
raise SystemExit(subprocess.run([*SSH,host,'python3 -c '+shlex.quote(code)],input=payload).returncode)
