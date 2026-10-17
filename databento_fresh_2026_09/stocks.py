from common import *
ROOT=P.parent;src=ROOT/'opening_leader_2026_07/data';univ=pd.read_parquet(src/'universe.parquet');panels=[]
for year in range(2018,2027):
 f=src/f'minutes_{year}.parquet'
 if not f.exists():continue
 a=pd.read_parquet(f);t=a.ts_event.dt.tz_convert('America/New_York');a['minute']=t.dt.hour*60+t.dt.minute
 keys=['day','instrument_id'];parts=[]
 for name,lo,hi,field in [('open',570,575,'open'),('signal',599,599,'close'),('entry',601,603,'open'),('exit',630,635,'open')]:
  b=a[a.minute.between(lo,hi)].sort_values('ts_event').groupby(keys).first();parts.append(b[field].rename(name))
 q=pd.concat(parts,axis=1).reset_index();ids=pd.read_parquet(src/f'idmap_{year}.parquet').drop_duplicates(keys);q=q.merge(ids[keys+['raw_symbol']],on=keys,how='left').rename(columns={'raw_symbol':'symbol'});q['month']=q.day.str[:7];q=q.merge(univ,on=['month','symbol'],how='inner');q['r']=q.signal/q.open-1;q=q[q[['open','signal','entry']].notna().all(axis=1)&(q[['open','signal','entry']]>0).all(axis=1)];q['missing_exit']=q.exit.isna();panels.append(q);print(year,len(q),q.missing_exit.sum(),flush=True)
a=pd.concat(panels);a.to_parquet(D/'stock_morning_panel.parquet');rows=[]
for universe in ['all','mega']:
 b=a if universe=='all' else a[a.mega];books={}
 for day,q in b.groupby('day'):
  if len(q)<20:continue
  q=q.copy();n=len(q);rank=q.r.rank(method='first');k=max(1,n//10);center=q.r-q.r.mean()
  weights={'decile':((rank>n-k).astype(float)-(rank<=k).astype(float))/(2*k),'continuous':center/center.abs().sum(),'long_control':pd.Series(1/n,index=q.index)}
  for label,w in weights.items():
   for sign,direction in ([(1,'follow'),(-1,'fade')] if label!='long_control' else [(1,'long')]):
    w=w*sign;ret=q.exit/q.entry-1
    # Missing exits retained, adverse 10% mark per allocated stock, explicitly audited.
    gross=(w*ret.fillna(0)).sum()-(w[q.missing_exit].abs()*.10).sum()
    for case,bps in [('base',5),('stress',15)]:
     fee=w.abs().sum()*2*bps/1e4;key=(case,label+'_'+direction);books.setdefault(key,[]).append(dict(day=pd.Timestamp(day),gross=gross,fees=fee,slippage=0.,activity=1.,net=gross-fee))
 for (case,label),records in books.items():
  z=pd.DataFrame(records).set_index('day');z.to_parquet(O/f'stocks_{case}_{label}_{universe}.parquet')
  for period,r in partitions(z).items():rows.append(dict(case=case,design=label,group=universe,period=period,**r))
pd.DataFrame(rows).to_csv(O/'stock_scoreboard.csv',index=False)
