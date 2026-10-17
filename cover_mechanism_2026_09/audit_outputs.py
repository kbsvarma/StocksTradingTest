from pathlib import Path
import json,pandas as pd,sys
P=Path(__file__).resolve().parent;V=P.parent/'cover_validation_2026_09';records=[]
sys.path.insert(0,str(V));from db_access import client,safe_error
try:jobs={z['id']:z for z in client.batch.list_jobs(since='2026-09-01')}
except Exception as e:print(safe_error(e));jobs={}
for folder in [V,P]:
 for f in folder.glob('SPEND_*.json'):
  for r in json.loads(f.read_text()):records.append(dict(folder=folder.name,ledger=f.name,name=r.get('name'),status=r.get('status'),reserved_usd=r['reserved_usd'],actual_cost_usd=jobs.get(r.get('job_id'),{}).get('cost_usd',r.get('actual_cost_usd')),quoted_cost_usd=r.get('quote_usd',r.get('quoted_usd'))))
x=pd.DataFrame(records);x.to_csv(P/'cost_reconciliation.csv',index=False);out=dict(reserved_total_usd=float(x.reserved_usd.sum()),known_actual_batch_usd=float(pd.to_numeric(x.actual_cost_usd,errors='coerce').sum()),balance_endpoint_verified=False,actual_or_quote_including_uncertain_usd=float(pd.to_numeric(x.actual_cost_usd,errors='coerce').fillna(pd.to_numeric(x.quoted_cost_usd,errors='coerce')).sum()))
assert out['reserved_total_usd']<=120
r=json.loads((P/'SPEND_BATCH.json').read_text());out['completed_batch_requests']=sum(z['status']=='complete' for z in r);out['expected_batch_requests']=len(r)
quality=[]
for file in (P/'batch').glob('**/condition.json'):
 for rec in json.loads(file.read_text()):quality.append(rec)
quality=pd.DataFrame(quality).drop_duplicates(['date','condition']);quality.to_csv(P/'option_vendor_conditions.csv',index=False);out['option_vendor_conditions']=quality.condition.value_counts().to_dict()
out['fresh_spy_files']=len(list((P/'fresh_spy').glob('*.parquet')))
for sym in ['spy','iwm']:
 x=pd.read_parquet(P/f'{sym}_hedge_trades.parquet');y=pd.read_parquet(P/f'{sym}_option_crp_trades.parquet');out[sym]=dict(hedge_rows=len(x),option_portfolio_rows=len(y),first=x.day.min(),last=x.day.max(),tenor_days=x.groupby('tenor').day.nunique().to_dict(),missing_exit_days=x[x.design.eq('unhedged')&x.case.eq('base')].groupby('tenor').missing_exit.sum().to_dict())
 assert not x.duplicated(['day','tenor','case','design']).any();assert not y.duplicated(['day','tenor','case','integer','design']).any();assert set(y.design)=={'hold','fill_initial','rebalance15','rebalance60'}
out['verification']=json.loads((P/'verification.json').read_text());(P/'audit.json').write_text(json.dumps(out,indent=2));print(json.dumps(out,indent=2))
