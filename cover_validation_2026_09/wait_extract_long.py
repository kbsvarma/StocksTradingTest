from pathlib import Path
import json,time,subprocess,sys
P=Path(__file__).resolve().parent
for ledger,name in [('SPEND_LONG_EXECUTION.json','execution_long_bbo')]:
 while True:
  rows=json.loads((P/ledger).read_text());r=next(x for x in rows if x['name']==name)
  if r['status']=='complete':break
  if r['status'] in ['download_failed','failed','expired','cancelled']:raise RuntimeError(r['status'])
  time.sleep(20)
 subprocess.run([sys.executable,str(P/'extract_execution.py'),name],check=True)
print('DONE all execution extraction',flush=True)
