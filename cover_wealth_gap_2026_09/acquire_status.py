from pathlib import Path
import sys,json
P=Path(__file__).resolve().parent;sys.path.insert(0,str(P.parent/'cover_validation_2026_09'));from db_access import client,safe_error
f=P/'SPEND_STATUS.json'
if f.exists():raise SystemExit('Request already reserved; inspect before retry')
p=json.loads((P/'quote.json').read_text())['params'];p['schema']='status';q=float(client.metadata.get_cost(**p));prior=json.loads((P/'SPEND_QUOTES.json').read_text());reserve=q*1.1+.01;assert prior['prior_actual_or_quote_including_uncertain']+prior['prior_unsettled_buffer']+prior['reserved_usd']+reserve<=120
r=dict(key_used='original',params=p,quote_usd=q,reserved_usd=reserve,status='reserved');f.write_text(json.dumps(r,indent=2))
try:
 x=client.timeseries.get_range(**p);x.to_file(P/'trading_status.dbn.zst');x.to_df().to_parquet(P/'trading_status.parquet');r['status']='complete';f.write_text(json.dumps(r,indent=2));print('status complete, quoted cost',q,flush=True)
except Exception as e:r['error']=safe_error(e);f.write_text(json.dumps(r,indent=2));print(r['error'],flush=True)
