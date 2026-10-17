from pathlib import Path
import sys,json,time
P=Path(__file__).resolve().parent;V=P.parent/'cover_validation_2026_09';sys.path.insert(0,str(V));from db_access import client,safe_error
f=P/'SPEND_QUOTES.json'
if f.exists():r=json.loads(f.read_text())
else:
 q=json.loads((P/'quote.json').read_text());old=json.loads((P.parent/'cover_mechanism_2026_09/audit.json').read_text());base=old['actual_or_quote_including_uncertain_usd'];buffer=(base-old['known_actual_batch_usd'])*.1;reserve=q['quote']*1.1+.01;assert base+buffer+reserve<=120
 r=dict(key_used='original',params=q['params'],quote_usd=q['quote'],reserved_usd=reserve,prior_actual_or_quote_including_uncertain=base,prior_unsettled_buffer=buffer,status='reserved');f.write_text(json.dumps(r,indent=2))
if r['status']=='complete':raise SystemExit('Already complete')
if 'job_id' not in r:
 j=client.batch.submit_job(**r['params'],split_duration='none');r['job_id']=j['id'];r['status']='submitted';f.write_text(json.dumps(r,indent=2));print('SUBMITTED original key; quote',r['quote_usd'],flush=True)
while True:
 j=next((x for x in client.batch.list_jobs(since='2026-09-13') if x['id']==r['job_id']),{})
 if j.get('state')=='done':
  paths=client.batch.download(r['job_id'],output_dir=P/'vendor');r['paths']=[str(x) for x in paths];r['status']='complete';r['actual_cost_usd']=j.get('cost_usd');f.write_text(json.dumps(r,indent=2));print('COMPLETE',r['actual_cost_usd'],flush=True);break
 if j.get('state') in ['expired','failed']:raise RuntimeError('Batch failed; inspect, do not silently switch keys')
 time.sleep(15)
