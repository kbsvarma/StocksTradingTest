from db_access import client,P,safe_error
import json
try:
 jobs={j['id']:j for j in client.batch.list_jobs(since='2026-09-13')}
 rows=[]
 for f in sorted(P.glob('SPEND_*.json')):
  for r in json.loads(f.read_text()):
   j=jobs.get(r.get('job_id'),{})
   rows.append(dict(ledger=f.name,name=r['name'],status=r['status'],job_id=r.get('job_id'),quote_usd=r['quote_usd'],reserved_usd=r['reserved_usd'],vendor_batch_cost_usd=j.get('cost_usd'),vendor_job_state=j.get('state')))
 batch=sum(float(r['vendor_batch_cost_usd'] or 0) for r in rows)
 stream=sum(r['quote_usd'] for r in rows if not r['job_id'])
 out=dict(known_batch_cost_usd=batch,stream_request_quotes_usd=stream,combined_batch_actual_plus_stream_quotes_usd=batch+stream,all_quotes_usd=sum(r['quote_usd'] for r in rows),all_reservations_usd=sum(r['reserved_usd'] for r in rows),credit_balance='unavailable through historical data key; not inferred',requests=rows)
 (P/'DATA_COST_RECONCILIATION.json').write_text(json.dumps(out,indent=2))
 print({k:v for k,v in out.items() if k!='requests'})
 print('missing batch costs',sum(r['job_id'] is not None and r['vendor_batch_cost_usd'] is None for r in rows))
except Exception as e: print(safe_error(e));raise SystemExit(1)
