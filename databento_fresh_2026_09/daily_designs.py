from common import *
@njit(cache=True)
def execute(gap,movement,rolls,first_roll,target,decision,fee,slip,mult):
 n=len(target);z=np.zeros((n,5));old=0.
 for i in range(n):
  new=target[i] if decision[i] else old;gross=old*gap[i]*mult+new*movement[i]*mult
  count=abs(new-old)
  if first_roll[i]:count=abs(old)+abs(new)
  count+=2*abs(new)*max(0.,rolls[i]-(1. if first_roll[i] else 0.));fc=count*fee/2;sc=count*slip
  if i==n-1:fc+=abs(new)*fee/2;sc+=abs(new)*slip
  z[i]=np.array([gross,fc,sc,(1. if abs(new)>0 else 0.),gross-fc-sc]);old=new
 return z

def run(s,d,forecast,clock,case):
 unit=100/d.atr/SPECS[s][0];target=(unit*forecast).replace([np.inf,-np.inf],0).fillna(0);decision=np.ones(len(d),dtype=bool) if clock=='daily' else (d.index.dayofweek==0);fee,slip=feespec(s,case);z=execute(d.gap.fillna(0).to_numpy(),d.move_after_open.fillna(0).to_numpy(),d.roll_count.fillna(0).to_numpy(),d.first_roll.fillna(False).astype(bool).to_numpy(),target.to_numpy(),decision & d.open.notna().to_numpy(),fee,slip,SPECS[s][0]);return pd.DataFrame(z,index=d.index,columns=['gross','fees','slippage','activity','net'])

def main():
 data={s:pd.read_parquet(D/f'daily_{s}.parquet') for s in SPECS};rows=[]
 groups={g:[s for s in SPECS if SPECS[s][2]==g] for g in ['equity','metal','currency','grain']};groups['all']=list(SPECS)
 for group,names in groups.items():
  ix=data[names[0]].index
  for s in names:ix=ix.union(data[s].index)
  ix=ix[ix>='2018'];ds={s:data[s].reindex(ix) for s in names};ret=pd.DataFrame({s:d.ret.fillna(0) for s,d in ds.items()});k=max(1,len(names)//3)
  for n in [5,20]:
   scores=ret.rolling(n).sum().shift();ranks=scores.rank(axis=1,method='first');forecast=((ranks>len(names)-k).astype(float)-(ranks<=k).astype(float))/k;forecast[scores.isna().any(axis=1)]=0.
   for sign,label in [(1,'momentum'),(-1,'reversal')]:
    for clock in ['daily','weekly']:
     for case in ['base','stress']:
      name=f'cross_{label}{n}_{clock}';books={s:run(s,ds[s],forecast[s]*sign,clock,case) for s in names};z=combine(books);z.to_parquet(O/f'daily_portfolio_{case}_{name}_{group}.parquet')
      for period,r in partitions(z).items():rows.append(dict(case=case,design=name,group=group,period=period,**r))
  print(group,'cross-sectional complete',flush=True)
 pairs=[('ES','NQ'),('ES','RTY'),('ES','EMD'),('GC','SI'),('HG','SI'),('6A','6B'),('6A','6J'),('ZC','ZS'),('ZC','ZW')]
 for left,right in pairs:
  ix=data[left].index.union(data[right].index);ix=ix[ix>='2018'];ds={s:data[s].reindex(ix) for s in [left,right]};rel=ds[left].ret.fillna(0)-ds[right].ret.fillna(0)
  for n in [20,60]:
   score=(rel.rolling(n).sum()/rel.rolling(n).std()/np.sqrt(n)).shift().clip(-1,1).fillna(0)
   for sign,label in [(1,'relative_momentum'),(-1,'relative_value')]:
    for clock in ['daily','weekly']:
     for case in ['base','stress']:
      name=f'pair_{label}{n}_{clock}';z=combine({left:run(left,ds[left],score*sign,clock,case),right:run(right,ds[right],-score*sign,clock,case)});group=left+'_'+right;z.to_parquet(O/f'daily_portfolio_{case}_{name}_{group}.parquet')
      for period,r in partitions(z).items():rows.append(dict(case=case,design=name,group=group,period=period,**r))
  print(left,right,'pair complete',flush=True)
 pd.DataFrame(rows).to_csv(O/'daily_scoreboard.csv',index=False)
if __name__=='__main__':main()
