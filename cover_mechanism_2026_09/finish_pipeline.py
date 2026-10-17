from pathlib import Path
import json,time,subprocess,sys
P=Path(__file__).resolve().parent;done=-1
while True:
 r=json.loads((P/'SPEND_BATCH.json').read_text());n=sum(z['status']=='complete' for z in r)
 if n!=done:
  print('completed requests',n,'of',len(r),flush=True);subprocess.run([sys.executable,str(P/'extract.py')],check=True);done=n
 if n==len(r):break
 time.sleep(30)
for name in ['options.py','company_portfolio.py','volatility_gate.py','summarize_options.py','delta_sensitivity.py','verify.py']:
 print('RUN',name,flush=True);subprocess.run([sys.executable,str(P/name)],check=True)
(P/'PIPELINE_COMPLETE').write_text('All requested batch files extracted and replays completed.');print('PIPELINE COMPLETE',flush=True)
