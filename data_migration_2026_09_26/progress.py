from pathlib import Path
import json,re,time,subprocess
C=Path('/home/varma/.data_migration_control_20260926');p=C/'transfer.log'
with p.open('rb') as f:f.seek(max(0,p.stat().st_size-6000));s=f.read().decode(errors='replace')
lines=[x.strip() for x in re.split('[\r\n]',s) if re.match(r'\s*[\d,]+\s+\d+%',x)]
line=lines[-1] if lines else ''
m=re.match(r'([\d,]+)',line);copied=int(m[1].replace(',','')) if m else None
r={'copied_GiB':round(copied/2**30,1) if copied else None,'source_GiB_approx':423.6,'latest_progress':line,'log_age_s':round(time.time()-p.stat().st_mtime)}
for name in ['transfer_result','verification_result']:
 q=C/(name+'.json')
 if q.exists():r[name]=json.loads(q.read_text())
print(json.dumps(r))
