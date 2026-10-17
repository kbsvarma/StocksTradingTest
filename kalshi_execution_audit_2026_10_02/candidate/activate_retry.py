"""User-run execution upgrade. Leaves sizing, risk caps and signal gates unchanged."""
import json,sys,subprocess,hashlib,shutil,time,sqlite3
from pathlib import Path
P=Path(__file__).resolve().parent;LIVE=Path('/home/varma/kalshi_revalidation_2026_09_12/live_v1');SERVICE='kalshi-crypto-z2-live.service'
if sys.argv[1:]!=['--execute']:raise SystemExit('Use --execute to activate the reviewed execution changes.')
for n,h in json.loads((P/'original_hashes.json').read_text()).items():
 if hashlib.sha256((LIVE/n).read_bytes()).hexdigest()!=h:raise SystemExit('Production changed since audit: '+n+'; refresh staging before activation.')
subprocess.run([sys.executable,'-m','unittest','test_engine','test_signals','test_retry','-q'],cwd=P,check=True)
sys.path.insert(0,str(LIVE))
from kalshi_client import KalshiClient,Credentials
from engine import Exchange,crypto,number
ex=Exchange(KalshiClient(Credentials.load(),dry_run=True))
def clear():
 if any(crypto(x.get('ticker','')) and abs(number(x,'position_fp','position')) for x in ex.positions()):raise RuntimeError('Open crypto position; wait for settlement.')
 if any(crypto(x.get('ticker','')) for x in ex.orders(status='resting')):raise RuntimeError('Resting orders require reconciliation.')
 c=sqlite3.connect('file:'+str(LIVE/'state/orders.sqlite3')+'?mode=ro',uri=True)
 try:rows=[json.loads(x[0]) for x in c.execute('select record from orders')]
 finally:c.close()
 if any(r['status'] in ('intent','unknown','ack','open','accounting_pending') for r in rows):raise RuntimeError('Pending ledger state; reconcile first.')
clear();backup=P/('pre_upgrade_'+str(int(time.time())));backup.mkdir()
files=['engine.py','runner.py','market_data.py','execution_retry.py']
for n in files:
 if (LIVE/n).exists():shutil.copy2(LIVE/n,backup/n)
subprocess.run(['systemctl','--user','stop',SERVICE],check=True)
try:
 clear()
 for n in files:
  temp=LIVE/(n+'.upgrade');shutil.copy2(P/n,temp);temp.replace(LIVE/n)
 subprocess.run([sys.executable,'runner.py','--preflight'],cwd=LIVE,check=True)
except BaseException:
 for n in files:
  if (backup/n).exists():shutil.copy2(backup/n,LIVE/n)
 subprocess.run(['systemctl','--user','start',SERVICE],check=True)
 raise
started=time.time();subprocess.run(['systemctl','--user','start',SERVICE],check=True)
for _ in range(25):
 time.sleep(1);d=json.loads((LIVE/'state/status.json').read_text())
 if d['ts']>=started and d.get('mode')=='LIVE' and d.get('armed') and not d.get('error') and not d.get('halted'):
  print('LIVE: bounded pre-submission book rechecks and prompt reconciliation. Signal gates, one IOC per market, size and risk limits unchanged.');break
else:raise SystemExit('Could not confirm healthy new heartbeat; inspect service before further changes.')
