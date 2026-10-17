from pathlib import Path
import pandas as pd,databento as db,json,numpy as np
P=Path(__file__).resolve().parent;O=P/'fresh_spy';O.mkdir(exist_ok=True);ledger=sum([json.loads(f.read_text()) for f in P.glob('SPEND_*.json')],[])
u=pd.read_parquet(P.parent/'trend_replication_2026_07/data/etf/SPY_1m.parquet');et=u.index.tz_convert('America/New_York');u=u[(et.hour==9)&(et.minute==59)];targets=pd.Series(u.close.round().to_numpy(),index=u.index.tz_convert('America/New_York').strftime('%Y-%m-%d')).to_dict()
for r in ledger:
 if r['status']!='complete' or not r['name'].startswith(('spy_atm','company_options')):continue
 marker=P/(r['name']+'.extracted')
 if marker.exists():continue
 files=[Path(z) for z in r.get('paths',[]) if str(z).endswith('.dbn.zst')] or [P/(r['name']+'.dbn.zst')];pieces=[];seen=0
 for f in files:
  for x in db.DBNStore.from_file(f).to_df(count=250000):
   seen+=len(x);et=x.index.tz_convert('America/New_York');minute=et.hour*60+et.minute
   if r['name'].startswith('spy_atm'):
    x=x[(minute>=595)&(minute<=950)].copy();x['day']=x.index.tz_convert('America/New_York').strftime('%Y-%m-%d');k=pd.to_numeric(x.symbol.str[-8:],errors='coerce')/1000;expiry=pd.to_datetime(x.symbol.str[-15:-9],format='%y%m%d',errors='coerce');dt=(expiry-pd.to_datetime(x.day)).dt.days;keep=k.eq(x.day.map(targets))&dt.between(0,7);x=x[keep]
   else:
    x=x[(minute>=943)&(minute<=946)].copy();x['day']=x.index.tz_convert('America/New_York').strftime('%Y-%m-%d')
   if len(x):pieces.append(x)
 if pieces:
  x=pd.concat(pieces);out=O if r['name'].startswith('spy_atm') else P/'company_quotes';out.mkdir(exist_ok=True)
  for day,g in x.groupby('day'):g.drop(columns='day').to_parquet(out/(day+'.parquet'))
 marker.write_text(str(seen));print(r['name'],seen,'records decoded',flush=True)
