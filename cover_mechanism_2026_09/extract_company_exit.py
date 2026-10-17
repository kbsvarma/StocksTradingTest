from pathlib import Path
import json,databento as db,pandas as pd
P=Path(__file__).resolve().parent;r=next(z for z in json.loads((P/'SPEND_BATCH.json').read_text()) if z['name']=='company_options_2025Q1');pieces=[]
for f in r['paths']:
 if not f.endswith('.dbn.zst'):continue
 for x in db.DBNStore.from_file(f).to_df(count=250000):
  et=x.index.tz_convert('America/New_York');m=et.hour*60+et.minute;x=x[(m>=945)&(m<=959)].copy()
  if len(x):x['day']=x.index.tz_convert('America/New_York').strftime('%Y-%m-%d');pieces.append(x)
o=P/'company_exit_quotes';o.mkdir(exist_ok=True)
for day,g in pd.concat(pieces).groupby('day'):g.drop(columns='day').to_parquet(o/f'{day}.parquet')
print('company exit window extracted',flush=True)
