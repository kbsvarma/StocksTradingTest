import json,time,datetime
from pathlib import Path
from db_access import client,safe_error
P=Path(__file__).resolve().parent;ledger=P/'SPEND_SUPPLEMENT.json';plans=[json.loads((P/'EXECUTION_SUPPLEMENT_PLAN.json').read_text())];records=json.loads(ledger.read_text()) if ledger.exists() else []
other=sum(x['reserved_usd'] for f in P.glob('SPEND_*.json') if f!=ledger for x in json.loads(f.read_text()))
assert other+sum(x.get('quote',0)*1.1+.01 for x in plans)+sum(x['reserved_usd'] for x in records)<120
for plan in plans:
 name=plan['name']
 if 'error' in plan:continue
 if any(x['name']==name for x in records):continue
 row={'name':name,'params':plan['params'],'quote_usd':plan['quote'],'reserved_usd':plan['quote']*1.1+.01,'status':'reserved','at':datetime.datetime.now(datetime.timezone.utc).isoformat()};records.append(row);ledger.write_text(json.dumps(records,indent=2))
 try:
  job=client.batch.submit_job(**plan['params'],split_duration='none');row['job_id']=job['id'];row['status']='submitted';(P/(name+'_job.json')).write_text(json.dumps(job,default=str,indent=2));print('SUBMITTED',name,job['id'],flush=True)
 except Exception as e:row['status']='failed_reservation_retained';row['error']=safe_error(e);print('FAILED',name,row['error'],flush=True)
 ledger.write_text(json.dumps(records,indent=2))
while any(x['status'] in ['submitted','queued','processing','received'] for x in records):
 jobs={x['id']:x for x in client.batch.list_jobs(since='2026-09-13')}
 for row in records:
  if row['status'] not in ['submitted','queued','processing','received']:continue
  j=jobs.get(row.get('job_id'));state=j.get('state') if j else None
  if state=='done':
   try:
    out=client.batch.download(row['job_id'],output_dir=P/'vendor'/row['name']);row['files']=[str(x) for x in out];row['status']='complete';print('DOWNLOADED',row['name'],len(out),flush=True)
   except Exception as e:row['status']='download_failed';row['error']=safe_error(e);print('DOWNLOAD ERROR',row['name'],row['error'],flush=True)
  elif state in ['expired','failed','cancelled']:row['status']=state;print('JOB',row['name'],state,flush=True)
 ledger.write_text(json.dumps(records,indent=2))
 pending=sum(x['status'] in ['submitted','queued','processing','received'] for x in records);print('PENDING',pending,flush=True)
 if pending:time.sleep(20)
print('DONE quoted',sum(x['quote_usd'] for x in records),'reserved',sum(x['reserved_usd'] for x in records),flush=True)
