from pathlib import Path
import subprocess,sys,json,time
P=Path(__file__).resolve().parent
while True:
 ledgers=all(all(x['status']=='complete' for x in json.loads((P/f).read_text())) for f in ['SPEND_EXPANSION.json','SPEND_FINAL_GAPS.json','SPEND_LONG_EXECUTION.json'])
 extracts=all((P/f).exists() and 'DONE all execution extraction' in (P/f).read_text() for f in ['extract.log','extract_long.log','extract_ceix.log'])
 if ledgers and extracts:break
 time.sleep(20)
for script in ['parse_closes.py','audit_vendor_closes.py','verify_pool_closes.py','execution_checks.py']:
 print('RUN',script,flush=True);subprocess.run([sys.executable,str(P/script)],check=True)
print('DONE final validation',flush=True)
