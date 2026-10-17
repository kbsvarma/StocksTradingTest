"""User-run activation of five-contract orders, preserving risk limits."""
import sys,subprocess,json,time,shutil,hashlib
from pathlib import Path
P=Path(__file__).resolve().parent
LIVE=Path('/home/varma/kalshi_revalidation_2026_09_12/live_v1')
if sys.argv[1:]!=['--execute']:raise SystemExit('Use --execute to enable five-contract real-money trading.')
subprocess.run([sys.executable,'-m','unittest','test_engine','test_signals','test_five','-q'],cwd=P,check=True)
sys.path.insert(0,str(LIVE))
from kalshi_client import KalshiClient,Credentials
from engine import Exchange
ex=Exchange(KalshiClient(Credentials.load(),dry_run=True))
def check():
 from engine import crypto,number
 assert not [x for x in ex.positions() if crypto(x.get('ticker','')) and abs(number(x,'position_fp','position'))], 'Open crypto positions; wait for settlement.'
 assert not [x for x in ex.orders(status='resting') if crypto(x.get('ticker',''))], 'Resting orders; reconcile first.'
 assert ex.balance()>=10,'Less than $10 cash'
 import sqlite3
 c=sqlite3.connect('file:'+str(LIVE/'state/orders.sqlite3')+'?mode=ro',uri=True)
 try:rows=[json.loads(x[0]) for x in c.execute('select record from orders')]
 finally:c.close()
 assert not [x for x in rows if x['status'] in ('intent','unknown','ack','open','accounting_pending')], 'Unresolved ledger orders'
check()
backup=P/('backup_'+str(int(time.time())));backup.mkdir()
for n in ['engine.py','sizing.py','runner.py']:shutil.copy2(LIVE/n,backup/n)
subprocess.run(['systemctl','--user','stop','kalshi-crypto-z2-live.service'],check=True)
try:
 check()
 for n in ['engine.py','sizing.py','runner.py']:
  tmp=LIVE/(n+'.scale5');shutil.copy2(P/n,tmp);tmp.replace(LIVE/n)
 subprocess.run([sys.executable,'runner.py','--preflight'],cwd=LIVE,check=True)
 (LIVE/'state/ARM_5CT').write_text('5\n')
except BaseException:
 for n in ['engine.py','sizing.py','runner.py']:shutil.copy2(backup/n,LIVE/n)
 subprocess.run(['systemctl','--user','start','kalshi-crypto-z2-live.service'],check=True)
 raise
subprocess.run(['systemctl','--user','start','kalshi-crypto-z2-live.service'],check=True)
for _ in range(20):
 time.sleep(1)
 d=json.loads((LIVE/'state/status.json').read_text())
 if d.get('contracts')==5 and d.get('armed') and time.time()-d['ts']<15:
  print('LIVE: 5 contracts; unchanged z2 entry rules; max8 contracts/$7.60 exposure/$10 daily loss budget.');break
else:raise SystemExit('Activation status not confirmed; inspect service logs before further changes.')
