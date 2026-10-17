from daily_designs import run
from common import *
import pyarrow.parquet as pq
f=P.parent/'convex_research_2026_07/data/glbx_stats.parquet';pieces=[]
for batch in pq.ParquetFile(f).iter_batches(columns=['ts_recv','instrument_id','symbol','stat_type','quantity','update_action'],batch_size=500000):
 a=batch.to_pandas().reset_index();a=a[(a.stat_type==9)&(a.quantity>0)&(a.quantity<2147483647)&(a.update_action==1)];pieces.append(a)
a=pd.concat(pieces).sort_values('ts_recv');a['date']=a.ts_recv.dt.tz_convert('America/New_York').dt.normalize();a=a.groupby(['date','instrument_id']).last().reset_index();a.to_parquet(D/'open_interest_received.parquet');rows=[];books={}
for s in SPECS:
 d=pd.read_parquet(D/f'daily_{s}.parquet');d=d[d.index>='2018'].copy();stats=a[a.symbol.eq(s+'.v.0')];lookup=stats.set_index(['date','instrument_id']).quantity;idx=pd.MultiIndex.from_arrays([d.index,d.last_id]);oi=pd.Series(lookup.reindex(idx).to_numpy(),index=d.index)
 same=d.last_id.eq(d.last_id.shift(5));change=(oi/oi.shift(5)-1).where(same);direction=np.sign(d.ret.rolling(5).sum());v=d.volume/d.volume.rolling(60).median();features={'oi_price_confirmation':direction*np.sign(change),'oi_contrarian':-direction*np.sign(change),'volume_price_confirmation':direction*np.sign(v-1),'volume_contrarian':-direction*np.sign(v-1),'slow_momentum60':np.sign(d.ret.rolling(60).sum()),'slow_momentum120':np.sign(d.ret.rolling(120).sum())}
 for name,feature in features.items():
  forecast=feature.shift().fillna(0)
  for case in ['base','stress']:
   z=run(s,d,forecast,'weekly',case);books.setdefault((case,name),{})[s]=z;z.to_parquet(O/f'innovation_{case}_{name}_{s}.parquet')
   for period,r in partitions(z).items():rows.append(dict(case=case,design=name,group=s,period=period,**r))
 print(s,'OI observations',oi.notna().sum(),flush=True)
for (case,name),parts in books.items():
 # Union dates: retain every leg's profit on non-overlapping market calendars.
 z=pd.concat({s:q for s,q in parts.items()}).groupby(level=1).sum();z.to_parquet(O/f'innovation_{case}_{name}_all.parquet')
 for period,r in partitions(z).items():rows.append(dict(case=case,design=name,group='all',period=period,**r))
pd.DataFrame(rows).to_csv(O/'innovation_scoreboard.csv',index=False)
