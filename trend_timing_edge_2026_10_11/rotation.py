from study import *
def extended(C):
 out=rules(C);lp=np.log(C);rank=(lp-lp.shift(7)).rank(axis=1,ascending=False,method='first');valid=lp.shift(30).notna();top=(rank<=2).astype(float)*2
 weekly=top.where(pd.DataFrame(np.tile((C.index.weekday==0)[:,None],(1,4)),index=C.index,columns=C.columns)).ffill().fillna(0)
 buffer=np.zeros(C.shape);held=set()
 for i in range(len(C)):
  ranking=rank.iloc[i]
  held={j for j in held if pd.notna(ranking.iloc[j]) and ranking.iloc[j]<=3}
  for j in np.argsort(ranking.fillna(100).to_numpy()):
   if len(held)>=2:break
   if ranking.iloc[j]<100 and pd.notna(ranking.iloc[j]):held.add(int(j))
  for j in held:buffer[i,j]=2
 choices={'weekly_top2':weekly,'buffer_top2':pd.DataFrame(buffer,index=C.index,columns=C.columns),'positive_top2':top.where(lp>lp.shift(7),0),'half_tilt_top2':.5+.5*top}
 for name,w in choices.items():out[name]=w.where(valid,0).shift(1).fillna(0).to_numpy()
 return {k:v for k,v in out.items() if k in ['always_long','LT30','top2_7']+list(choices)}
def transfer_build(stress):
 Cs=[];Os=[];Fs=[]
 for asset in ['LTC','DOGE','ADA','BCH']:
  raw=json.load(gzip.open(P/'data'/f'{asset}.json.gz','rt'));d=pd.DataFrame(raw['rows'],columns=['t','low','high','open','close','volume']);d.index=pd.to_datetime(d.t,unit='s',utc=True);d=d.sort_index();d[['open','close']]*=1e-6
  c=d.close.resample('1D').last();h='01:00' if stress else '00:00';op=d.open.between_time(h,h);op.index=op.index.floor('D');Cs.append(c.rename(asset));Os.append(pd.concat([op.rename(asset+'_b'),op.rename(asset+'_a')],axis=1));Fs.append(pd.Series(0.,index=c.index,name=asset))
 C=pd.concat(Cs,axis=1);idx=pd.date_range(C.index.min(),C.index.max(),tz='UTC');return C.reindex(idx),pd.concat(Os,axis=1).reindex(idx),pd.concat(Fs,axis=1).reindex(idx)
def main(transfer=False):
 rows=[]
 sources=['transfer'] if transfer else ['proxy','venue']
 for source in sources:
  for stress in [False,True]:
   C,O,F=transfer_build(stress) if transfer else old.build(source,stress);signals=extended(C);src='proxy' if transfer else source
   for name,S in signals.items():
    for fs in [0.,1.]:
     for per,st,en in periods(C,src):
      r,path,pnl=old.simulate(C,O,F,S,src,stress,st,en,list(range(4)),fs);r.update(source=source,design=name,scenario='combined' if stress else 'base',funding_enabled=bool(fs),period=per,asset_pnl=pnl.tolist());rows.append(r)
      np.savez_compressed(P/'ledgers'/f'rotation_{source}_{name}_{fs}_{per}_{stress}.npz',equity=path,asset_pnl=pnl)
  print(source,'done',flush=True)
 pd.DataFrame(rows).to_csv(P/('transfer_results.csv' if transfer else 'rotation_results.csv'),index=False)
if __name__=='__main__':main('--transfer' in sys.argv)
