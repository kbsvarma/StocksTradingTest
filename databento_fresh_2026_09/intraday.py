from common import *

def main():
 frames={s:pd.read_parquet(D/f'intraday_{s}.parquet') for s in SPECS};rows=[];books={};ledgers=[]
 for sym,q in frames.items():
  mult,tick,group=SPECS[sym];q=q[q.index>='2018'].copy()
  for name,feature,entry in [('opening30','r30','o1001'),('opening60','r60','o1031'),('overnight','gap','o1001'),('relative30','relative30','o1001')]:
   if feature=='relative30':
    peers=[s for s in SPECS if SPECS[s][2]==group and s!=sym]
    if not peers:continue
    peer=pd.concat([frames[s].r30.rename(s) for s in peers],axis=1).mean(axis=1);signal=q.r30-peer.reindex(q.index)
   else:signal=q[feature]
   known=q[['atr',entry]].notna().all(axis=1)&signal.notna()&(q.atr>0);qty=(100/q.atr/mult).where(known,0);signal=np.sign(signal.fillna(0))
   for direction in [1,-1]:
    label=name+('_follow' if direction==1 else '_fade');target=qty*signal*direction
    for case in ['base','stress']:
     fee,slip=feespec(sym,case);gross=target*(q.o1245-q[entry]).fillna(0)*mult-target.abs()*q.atr.where(q.o1245.isna(),0)*mult*2;z=pd.DataFrame({'gross':gross,'fees':target.abs()*fee,'slippage':target.abs()*2*slip,'activity':target.abs()>0},index=q.index);z['net']=z.gross-z.fees-z.slippage;z['activity']=z.activity.astype(float);books.setdefault((case,label),{})[sym]=z;z.to_parquet(O/f'intra_{case}_{label}_{sym}.parquet')
     for period,r in partitions(z).items():rows.append(dict(case=case,design=label,symbol=sym,group=group,period=period,**r))
  print(sym,'intraday complete',flush=True)
 pd.DataFrame(rows).to_csv(O/'intraday_market.csv',index=False);rows=[]
 for (case,label),parts in books.items():
  for group in ['all',*sorted(set(v[2] for v in SPECS.values()))]:
   use={s:p for s,p in parts.items() if group=='all' or SPECS[s][2]==group}
   if len(use)<2:continue
   z=combine(use);z.to_parquet(O/f'intra_portfolio_{case}_{label}_{group}.parquet')
   for period,r in partitions(z).items():rows.append(dict(case=case,design=label,group=group,markets=len(use),period=period,**r))
 a=pd.DataFrame(rows);a.to_csv(O/'intraday_scoreboard.csv',index=False);print(a[(a.period.isin(['development','validation']))&(a.group=='all')][['case','design','period','net','sharpe']].round(3).to_string(index=False),flush=True)
if __name__=='__main__':main()
