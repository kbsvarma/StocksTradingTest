from pathlib import Path
import pandas as pd,json
from db_access import download
P=Path(__file__).resolve().parent;pool=pd.concat([pd.read_csv(P/f) for f in ['verification_pool_extended.csv','verification_pool_expanded.csv','verification_pool_long.csv']]);sy=set(pool.a)|set(pool.b)
for k,intervals in json.loads((P/'issuer_alias_intervals.json').read_text()).items():
 if k in sy:sy.update(x[0] for x in intervals)
sy.add('BRK/B');data=download('trading_status',dict(dataset='XNAS.ITCH',symbols=sorted(sy),schema='status',start='2018-05-01',end='2026-09-12'),ledger_name='SPEND_STATUS.json');x=data.to_df();x.to_parquet(P/'trading_status.parquet');print('DONE',x.shape,x.columns.tolist(),flush=True);print(x[['action','is_trading','is_quoting']].value_counts().head(15).to_string(),flush=True)
