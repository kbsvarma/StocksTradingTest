from pathlib import Path
import json,time
from db_access import client,safe_error
P=Path(__file__).resolve().parent;f=P/'SPEND_EXPANSION.json';rows=json.loads(f.read_text())
for r in rows:
 if r['status']!='failed_reservation_retained' or '429 Too Many Requests' not in r.get('error',''):continue
 assert not r.get('job_id')
 try:
  j=client.batch.submit_job(**r['params'],split_duration='none');r['prior_submit_error']=r.pop('error');r['job_id']=j['id'];r['status']='submitted';print('RESUBMITTED previously rejected',r['name'],j['id'],flush=True)
 except Exception as e:r['error']=safe_error(e);print('ERROR',r['name'],r['error'],flush=True)
 f.write_text(json.dumps(rows,indent=2));time.sleep(15)
while any(r['status']=='submitted' for r in rows):
 jobs={j['id']:j for j in client.batch.list_jobs(since='2026-09-13')}
 for r in rows:
  if r['status']!='submitted':continue
  j=jobs.get(r['job_id'],{})
  if j.get('state')=='done':
   try:r['files']=[str(x) for x in client.batch.download(r['job_id'],output_dir=P/'vendor'/r['name'])];r['status']='complete';print('DOWNLOADED',r['name'],flush=True)
   except Exception as e:r['status']='download_failed';r['error']=safe_error(e)
  elif j.get('state') in ['failed','expired','cancelled']:r['status']=j['state']
 f.write_text(json.dumps(rows,indent=2))
 if any(r['status']=='submitted' for r in rows):time.sleep(30)
print('DONE',flush=True)
