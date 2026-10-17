from pathlib import Path
import pandas as pd,numpy as np,databento as db
import sys
P=Path(__file__).resolve().parent;name=sys.argv[1] if len(sys.argv)>1 else 'execution_extended_bbo';files=list((P/'vendor'/name).glob('**/*.dbn.zst'));parts=[]
assert files,'Execution file not ready'
for f in files:
 for k,x in enumerate(db.DBNStore.from_file(f).to_df(count=250000)):
  times=x.index.tz_convert('America/New_York');mins=times.hour*60+times.minute
  keep=np.isin(mins,[12*60+57,12*60+58,12*60+59,13*60,15*60+57,15*60+58,15*60+59,16*60]);z=x.loc[keep].copy()
  if len(z):
   z['local_time']=times[keep];z['date']=times[keep].tz_localize(None).normalize();z['minute']=mins[keep];parts.append(z.reset_index())
  if k%40==0:print('chunks',k,'kept',sum(len(z) for z in parts),flush=True)
x=pd.concat(parts,ignore_index=True);x.to_parquet(P/('execution_snapshots_'+name+'.parquet'),index=False);print('DONE',x.shape,x.columns.tolist(),flush=True)
