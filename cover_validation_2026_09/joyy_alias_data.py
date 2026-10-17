from pathlib import Path
import json,pandas as pd
from db_access import download
P=Path(__file__).resolve().parent;f=P/'issuer_alias_intervals.json';a=json.loads(f.read_text());a['JOYY']=[['YY','2018-05-01','2025-03-31'],['JOYY','2025-03-31','2026-09-12']];a['AXON']=[['AAXN','2018-05-01','2021-01-26'],['AXON','2021-01-26','2026-09-12']];a['WTW']=[['WLTW','2018-05-01','2022-01-10'],['WTW','2022-01-10','2026-09-12']];f.write_text(json.dumps(a,indent=2))
for name,symbol,end in [('yy_bbo','YY','2025-03-31')]:
 data=download(name,dict(dataset='XNAS.ITCH',symbols=[symbol],schema='bbo-1m',start='2018-05-01',end=end),ledger_name='SPEND_LATE_ALIASES.json');x=data.to_df();t=x.index.tz_convert('America/New_York');mins=t.hour*60+t.minute;keep=mins.isin([777,778,779,780,957,958,959,960]);z=x.loc[keep].copy();z['date']=t[keep].tz_localize(None).normalize();z['minute']=mins[keep];z.reset_index().to_parquet(P/('execution_snapshots_'+name+'.parquet'),index=False);print(name,len(z),flush=True)
