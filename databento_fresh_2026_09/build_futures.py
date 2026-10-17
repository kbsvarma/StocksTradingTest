from pathlib import Path
import numpy as np,pandas as pd,json
P=Path(__file__).resolve().parent;ROOT=P.parent;O=P/'data';O.mkdir(exist_ok=True)
SPECS={'ES':(5,.25,'equity'),'NQ':(2,.25,'equity'),'YM':(.5,1,'equity'),'RTY':(50,.1,'equity'),'EMD':(100,.1,'equity'),'NKD':(5,5,'equity'),'GC':(100,.1,'metal'),'SI':(5000,.005,'metal'),'HG':(25000,.0005,'metal'),'CL':(1000,.01,'energy'),'6A':(100000,.00005,'currency'),'6B':(62500,.0001,'currency'),'6J':(12500000,.0000005,'currency'),'ZN':(1000,1/64,'rates'),'ZC':(50,.25,'grain'),'ZS':(50,.25,'grain'),'ZW':(50,.25,'grain'),'BTC':(5,5,'crypto')}

def load(sym):
 if sym in ['ES','NQ','GC','CL']:fs=[ROOT/'spx_spread_bot/backtests/futures_intraday'/f'{sym}_1m.parquet']
 else:fs=sorted((ROOT/'trend_replication_2026_07/data').glob(f'{sym}_1m_*.parquet'))
 a=pd.concat([pd.read_parquet(f) for f in fs]).sort_index();a=a[~a.index.duplicated(keep='first')];return a,fs

def main():
 coverage=[]
 for sym,(mult,tick,group) in SPECS.items():
  a,fs=load(sym);a=a[(a.index>='2016')&(a.index<'2026-06-03')];et=a.index.tz_convert('America/New_York');a['date']=et.normalize();a['minute']=et.hour*60+et.minute;g=a.groupby('date');d=g.agg(open=('open','first'),high=('high','max'),low=('low','min'),close=('close','last'),volume=('volume','sum'),first_id=('instrument_id','first'),last_id=('instrument_id','last'));d['roll']=d.first_id.ne(d.last_id)|d.first_id.ne(d.last_id.shift());change=a.instrument_id.ne(a.instrument_id.shift());change.iloc[0]=False;seam=(a.open-a.close.shift()).where(change,0).fillna(0);adjust=seam.cumsum();clean=a[['high','low','close']].sub(adjust,axis=0);clean['date']=a.date;cd=clean.groupby('date').agg(high=('high','max'),low=('low','min'),close=('close','last'));prev=cd.close.shift();tr=pd.concat([cd.high-cd.low,(cd.high-prev).abs(),(cd.low-prev).abs()],axis=1).max(axis=1);d['atr']=tr.rolling(20).mean().shift();move=a.close.diff().fillna(0)-seam;d['clean_move']=move.groupby(a.date).sum();d['gap']=(d.open-d.close.shift()).where(d.first_id.eq(d.last_id.shift()),0).fillna(0);d['move_after_open']=d.clean_move-d.gap;d['roll_count']=change.groupby(a.date).sum();d['first_roll']=d.first_id.ne(d.last_id.shift());d.iloc[0,d.columns.get_loc('first_roll')]=False;d['ret']=d.clean_move/d.atr;d.to_parquet(O/f'daily_{sym}.parquet')
  q=d[['atr']].copy()
  for minute,name,field in [(570,'o930','open'),(599,'c959','close'),(601,'o1001','open'),(629,'c1029','close'),(631,'o1031','open'),(765,'o1245','open'),(959,'c1559','close')]:
   b=a[a.minute==minute].set_index('date');q[name]=b[field];q[name+'_id']=b.instrument_id
  q['prev1559']=q.c1559.shift();q['prev1559_id']=q.c1559_id.shift();q['vol30']=a[a.minute.between(570,599)].groupby('date').volume.sum();q['vol60']=a[a.minute.between(570,629)].groupby('date').volume.sum();q['vol30_rel']=q.vol30/q.vol30.rolling(20).median().shift();q['vol60_rel']=q.vol60/q.vol60.rolling(20).median().shift();q['r30']=(q.c959-q.o930)/q.atr;q['r60']=(q.c1029-q.o930)/q.atr;q['gap']=(q.o930-q.prev1559)/q.atr;q.loc[q.o930_id.ne(q.prev1559_id),'gap']=np.nan
  for en in ['o1001','o1031']:
   ok=q[en].notna()&q.o1245.notna();bad=ok&q[en+'_id'].ne(q.o1245_id);assert not bad.any(),(sym,'intraday roll needs explicit handling',q[bad].index.tolist()[:5])
  q.to_parquet(O/f'intraday_{sym}.parquet');coverage.append(dict(symbol=sym,group=group,rows=len(a),start=str(a.index.min()),end=str(a.index.max()),intraday_days=int(q[['atr','o930','c959','o1001','o1245']].notna().all(axis=1).sum()),sources='|'.join(str(f.relative_to(ROOT)) for f in fs)));print(sym,len(a),coverage[-1]['intraday_days'],flush=True)
 pd.DataFrame(coverage).to_csv(P/'futures_coverage.csv',index=False);(P/'contract_assumptions.json').write_text(json.dumps(SPECS,indent=2))
if __name__=='__main__':main()
