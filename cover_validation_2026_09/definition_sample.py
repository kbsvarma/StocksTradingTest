from db_access import download,P
import pandas as pd
for name,start,end in [('2018','2018-05-01','2018-05-02'),('2022','2022-01-03','2022-01-04'),('2026','2026-09-11','2026-09-12')]:
 x=download('definition_'+name,dict(dataset='XNAS.ITCH',symbols='ALL_SYMBOLS',schema='definition',start=start,end=end),ledger_name='SPEND_DEFINITIONS.json').to_df();x.to_parquet(P/('definition_'+name+'.parquet'));print(name,x.shape,x.columns.tolist(),flush=True)
