from pathlib import Path
import sys,json
P=Path(__file__).resolve().parent;V=P.parent/'cover_validation_2026_09';sys.path.insert(0,str(V));from db_access import client,safe_error
f=P/'SPEND_LIQUID.json'
if f.exists():raise SystemExit('Already reserved: inspect status before retry')
x=json.loads((P/'liquid_futures_quote.json').read_text());reserve=x['quote']*1.1+.01
prior=sum(r['reserved_usd'] for d in [P,V] for z in d.glob('SPEND_*.json') for r in json.loads(z.read_text()));assert prior+reserve<=120
r=dict(name='liquid_futures',quote_usd=x['quote'],reserved_usd=reserve,status='started',params=x['params']);f.write_text(json.dumps([r],indent=2))
try:
 data=client.timeseries.get_range(**x['params']);data.to_file(P/'liquid_futures.dbn.zst');data.to_df().to_parquet(P/'liquid_futures.parquet');r['status']='complete';f.write_text(json.dumps([r],indent=2));print('complete liquid futures',flush=True)
except Exception as e:r['error']=safe_error(e);f.write_text(json.dumps([r],indent=2));print(r['error'])
