from common import *
from daily_designs import run
specs={'DX':(1000,.005,'dollar'),'CT':(500,.01,'soft'),'KC':(375,.05,'soft'),'SB':(1120,.01,'soft'),'CC':(10,1,'soft'),'BRN':(1000,.01,'energy')};SPECS.update(specs);books={};rows=[];audit=[]
for fname in ['ifus_1h','ifeu_1h']:
 raw=pd.read_parquet(P.parent/'convex_research_2026_07/data'/f'{fname}.parquet')
 for symbol,b in raw.groupby('symbol'):
  s=symbol.split('.')[0];pre=b[b.index<'2020'];pub=pre.groupby('publisher_id').volume.sum().idxmax();a=b[b.publisher_id==pub].sort_index();a=a[~a.index.duplicated()];a['date']=a.index.tz_convert('America/New_York').normalize();change=a.instrument_id.ne(a.instrument_id.shift());change.iloc[0]=False;seam=(a.open-a.close.shift()).where(change,0).fillna(0);adjust=seam.cumsum();clean=a[['high','low','close']].sub(adjust,axis=0);clean['date']=a.date;cd=clean.groupby('date').agg(high=('high','max'),low=('low','min'),close=('close','last'));prev=cd.close.shift();tr=pd.concat([cd.high-cd.low,(cd.high-prev).abs(),(cd.low-prev).abs()],axis=1).max(axis=1);d=a.groupby('date').agg(open=('open','first'),close=('close','last'),first_id=('instrument_id','first'),last_id=('instrument_id','last'));d['atr']=tr.rolling(20).mean().shift();move=a.close.diff().fillna(0)-seam;d['clean_move']=move.groupby(a.date).sum();d['gap']=(d.open-d.close.shift()).where(d.first_id.eq(d.last_id.shift()),0).fillna(0);d['move_after_open']=d.clean_move-d.gap;d['roll_count']=change.groupby(a.date).sum();d['first_roll']=d.first_id.ne(d.last_id.shift());d.iloc[0,d.columns.get_loc('first_roll')]=False;d['ret']=d.clean_move/d.atr;d=d[d.index>='2020'];d.to_parquet(D/f'daily_{s}.parquet');audit.append(dict(symbol=s,publisher=int(pub),publisher_selection='2019 volume only',rows=len(a)))
  for n in [20,60,120]:
   for sign,label in [(1,'momentum'),(-1,'reversal')]:
    forecast=np.sign(d.ret.rolling(n).sum()).shift().fillna(0)*sign
    for case in ['base','stress']:
     name=f'ice_{label}{n}';z=run(s,d,forecast,'weekly',case);books.setdefault((case,name),{})[s]=z;z.to_parquet(O/f'{name}_{case}_{s}.parquet')
     for period,r in partitions(z).items():rows.append(dict(case=case,design=name,group=s,period=period,**r))
for (case,name),parts in books.items():
 z=pd.concat(parts).groupby(level=1).sum();z.to_parquet(O/f'{name}_{case}_all.parquet')
 for period,r in partitions(z).items():rows.append(dict(case=case,design=name,group='all',period=period,**r))
pd.DataFrame(rows).to_csv(O/'ice_scoreboard.csv',index=False);pd.DataFrame(audit).to_csv(P/'ice_audit.csv',index=False)
