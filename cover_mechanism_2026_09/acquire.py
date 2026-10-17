from pathlib import Path
import json,sys
P=Path(__file__).resolve().parent;V=P.parent/'cover_validation_2026_09';sys.path.insert(0,str(V));import db_access as db
prior=sum(r['reserved_usd'] for f in V.glob('SPEND_*.json') for r in json.loads(f.read_text()));plans=json.loads((P/'spy_missing_plan.json').read_text())+json.loads((P/'company_option_plan.json').read_text());assert prior+sum(r['quote']*1.1+.01 for r in plans)<=120
print('prior reservations',prior,'new quotes',sum(r['quote'] for r in plans),'combined reserved',prior+sum(r['quote']*1.1+.01 for r in plans),flush=True);db.P=P
for r in plans:
 try:
  db.download(r['name'],r['params'],cap=120-prior,ledger_name='SPEND_MECHANISM.json');print('DONE',r['name'],flush=True)
 except Exception as e:print('FAILED',r['name'],db.safe_error(e),flush=True)
