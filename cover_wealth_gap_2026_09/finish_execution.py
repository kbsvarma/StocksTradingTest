from pathlib import Path
import sys,json,subprocess,time
P=Path(__file__).resolve().parent
while json.loads((P/'SPEND_QUOTES.json').read_text())['status']!='complete':time.sleep(20)
for name in ['prepare_execution.py','execution_checks.py']:
 print('RUN',name,flush=True);subprocess.run([sys.executable,str(P/name)],check=True)
(P/'EXECUTION_COMPLETE').write_text('Complete');print('EXECUTION COMPLETE',flush=True)
