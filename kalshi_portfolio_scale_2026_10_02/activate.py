"""User-run activation of five-contract orders, preserving risk limits."""
import sys,subprocess,json,time,shutil,hashlib
from pathlib import Path
P=Path(__file__).resolve().parent
LIVE=Path('/home/varma/kalshi_revalidation_2026_09_12/live_v1')
import argparse
a=argparse.ArgumentParser();a.add_argument('--execute',action='store_true');a.add_argument('--daily-budget',choices=['10','25'],required=True);args=a.parse_args()
if not args.execute:raise SystemExit('User-run activation requires --execute')
expected=json.loads((P/'original_hashes.json').read_text())
for name,h in expected.items():
 if hashlib.sha256((LIVE/name).read_bytes()).hexdigest()!=h:raise SystemExit('Production changed since staging: '+name)
subprocess.run([sys.executable,'-m','unittest','test_portfolio','-q'],cwd=P,check=True)
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
for n in ['engine.py','sizing.py']:shutil.copy2(LIVE/n,backup/n)
subprocess.run(['systemctl','--user','stop','kalshi-crypto-z2-live.service'],check=True)
try:
 check()
 for n in ['engine.py','sizing.py']:
  tmp=LIVE/(n+'.portfolio');content=(P/n).read_text()
  if n=='sizing.py':content=content.replace("DAILY_LOSS_BUDGET = Decimal('10')","DAILY_LOSS_BUDGET = Decimal('"+args.daily_budget+"')")
  tmp.write_text(content);tmp.replace(LIVE/n)
 subprocess.run([sys.executable,'runner.py','--preflight'],cwd=LIVE,check=True)
 # Existing five-contract arming is preserved.
except BaseException:
 for n in ['engine.py','sizing.py']:shutil.copy2(backup/n,LIVE/n)
 subprocess.run(['systemctl','--user','start','kalshi-crypto-z2-live.service'],check=True)
 raise
started=time.time()
subprocess.run(['systemctl','--user','start','kalshi-crypto-z2-live.service'],check=True)
for _ in range(20):
 time.sleep(1)
 d=json.loads((LIVE/'state/status.json').read_text())
 if d.get('contracts')==5 and d.get('max_contracts')==20 and d.get('max_risk')=='19.00' and d.get('daily_loss_budget')==args.daily_budget and d.get('armed') and d['ts']>=started and not d.get('error') and not d.get('halted'):
  print('LIVE: 5 per market; max20 contracts/$19 exposure/$'+args.daily_budget+' daily loss budget.');break
else:raise SystemExit('Activation status not confirmed; inspect service logs before further changes.')
