from pathlib import Path
import sys,json,time
P=Path(__file__).resolve().parent;V=P.parent/'cover_validation_2026_09';sys.path.insert(0,str(V));from db_access import client,safe_error
ledger=P/'SPEND_BATCH.json';records=json.loads(ledger.read_text()) if ledger.exists() else [];plans=json.loads((P/'spy_missing_plan.json').read_text())+json.loads((P/'company_option_plan.json').read_text());old=json.loads((P/'SPEND_MECHANISM.json').read_text());done={r['name'] for r in old if r['status']=='complete'}
for plan in plans:
 if plan['name'] in done or any(r['name']==plan['name'] for r in records):continue
 prior=sum(r['reserved_usd'] for folder in [P,V] for f in folder.glob('SPEND_*.json') if f!=ledger for r in json.loads(f.read_text()));reserve=plan['quote']*1.1+.01;assert prior+sum(r['reserved_usd'] for r in records)+reserve<=120
 records.append(dict(name=plan['name'],params=plan['params'],quote_usd=plan['quote'],reserved_usd=reserve,status='reserved'))
ledger.write_text(json.dumps(records,indent=2))
records.sort(key=lambda r: (0 if r['name'].startswith('company') else 1,r['name']))
while any(r['status']!='complete' for r in records):
 jobs={j['id']:j for j in client.batch.list_jobs(since='2026-09-13')};active=sum(r['status']=='submitted' for r in records)
 for r in records:
  if r['status']=='submitted':
   j=jobs.get(r['job_id'],{})
   if j.get('state')=='done':
    try:
     paths=client.batch.download(r['job_id'],output_dir=P/'batch'/r['name']);r['paths']=[str(x) for x in paths];r['actual_cost_usd']=j.get('cost_usd');r['status']='complete';active-=1;print('COMPLETE',r['name'],flush=True)
    except Exception as e:print('download error',safe_error(e),flush=True)
  if r['status']=='reserved' and active<23:
   try:
    job=client.batch.submit_job(**r['params'],split_duration='none');r['job_id']=job['id'];r['status']='submitted';active+=1;print('SUBMITTED',r['name'],flush=True)
   except Exception as e:print('submit deferred',r['name'],safe_error(e),flush=True);break
  ledger.write_text(json.dumps(records,indent=2))
 if any(r['status']!='complete' for r in records):time.sleep(15)
