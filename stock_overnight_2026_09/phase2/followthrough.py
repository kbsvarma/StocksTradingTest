from pathlib import Path
import numpy as np,pandas as pd,json
P=Path(__file__).resolve().parent;B=P.parent
alias={'FB':'META','SQ':'XYZ','ANTM':'ELV'}
tr=pd.read_parquet(P/'trades.parquet');sel=pd.read_csv(P/'selection.csv');daily=pd.read_csv(P/'daily.csv',parse_dates=['entry','exit']);cache={};px=[]
for symbol in tr.symbol.unique():
 f=B/'data'/f'{alias.get(symbol,symbol)}.parquet'
 b=pd.read_parquet(f);b.index=pd.to_datetime(b.index.date)
 split=b['Stock Splits'].replace(0,1).fillna(1);factor=split.iloc[::-1].cumprod().iloc[::-1]/split
 q=pd.DataFrame({'day':b.index,'symbol':symbol,'factor':factor.to_numpy(),'close':b.Close.to_numpy(),'dailyopen':b.Open.to_numpy(),'div':b.Dividends.fillna(0).to_numpy(),'dailyvolume':b.Volume.to_numpy()})
 cache[symbol]=q;px.append(q)
px=pd.concat(px,ignore_index=True)
tr=tr.merge(px.rename(columns={'day':'entry','factor':'entry_factor','close':'entry_close'})[['entry','symbol','entry_factor','entry_close']],on=['entry','symbol'],validate='many_to_one')
tr=tr.merge(px.rename(columns={'day':'exit'})[['exit','symbol','factor','div','dailyopen']],on=['exit','symbol'],validate='many_to_one')
archive=pd.read_parquet(P/'archive_opens.parquet');archive=archive[archive.volume>0]
wide=archive.pivot(index=['exit','symbol'],columns='minute',values='open').rename(columns={570:'m0930',571:'m0931',575:'m0935',585:'m0945',600:'m1000'}).reset_index()
tr=tr.merge(wide,on=['exit','symbol'],how='left',validate='many_to_one');times=['m0930','m0931','m0935','m0945','m1000']
for col in times:tr[col+'_ret']=(tr[col]/tr.factor+tr['div'])/tr.entry_close-1
tr['entry_nominal']=tr.entry_close*tr.entry_factor
tr.to_parquet(P/'execution_trades.parquet',index=False)
# Use exact same stock/night observations for every timing; don't silently replace absent quotes.
common=tr[times].notna().all(axis=1)&~tr.missing_exit
end=archive.exit.max();starts={'All':'2018-01-01','2022+':'2022-01-01','2025+':'2025-01-01'}
rows=[]
for strategy in ['OBG','TopDecile','Universe']:
 for period,start in starts.items():
  g=tr[(tr.strategy==strategy)&(tr.entry>=start)&(tr.exit<=end)];h=g[common.loc[g.index]]
  for timing in ['ret']+[x+'_ret' for x in times]:
   vals=h[timing];rows.append(dict(strategy=strategy,period=period,timing=timing,pairs=len(h),expected=len(g),weight_coverage=h.weight.sum()/g.weight.sum(),weighted_mean_bps=np.average(vals,weights=h.weight)*1e4))
pd.DataFrame(rows).to_csv(P/'timing_matched.csv',index=False)
# Broker-cost scenarios: continuous bankroll, whole shares, initial capital reset for each reported period.
# Fixed plan/current fee schedule as sensitivity, not historical invoice reconstruction.
results=[];book=[]
for strategy in ['OBG','TopDecile']:
 for period,start in starts.items():
  gd=daily[(daily.strategy==strategy)&(daily.entry>=start)];g=tr[(tr.strategy==strategy)&(tr.entry>=start)]
  for mode in ['daily_open','matched_daily','matched_0935']:
   hh=g if mode=='daily_open' else g[(g.exit<=end)&common.loc[g.index]]
   dd=gd if mode=='daily_open' else gd[gd.exit<=end]
   groups={date:v for date,v in hh.groupby('entry')}
   for capital in [25000,100000,250000]:
    for slip in [0,3,10]:
     equity=float(capital);path=[];feesum=slipsum=turns=0;grosssum=0
     for row in dd.itertuples():
      v=groups.get(row.entry);gross=fees=friction=0.;count=0
      if v is not None:
       # Reserve conservative commissions and friction before allocating shares. No borrow.
       budget=equity/(1+2*.005/5+2/ max(equity/max(row.names,1),1)+.0001+slip/1e4)
       shares=np.floor((budget*v.weight)/v.entry_nominal).clip(lower=0).to_numpy();entryprice=v.entry_nominal.to_numpy();valid=shares>0
       rr=v.ret.to_numpy() if mode!='matched_0935' else v.m0935_ret.to_numpy()
       notional=shares*entryprice
       gross=float((notional*rr).sum());sellnotional=np.maximum(notional*(1+rr)-shares*v.entry_factor.to_numpy()*v["div"].to_numpy(),0);sellshares=shares*v.entry_factor.to_numpy()/v.factor.to_numpy()
       buyfee=np.where(valid,np.minimum(np.maximum(shares*.005,1),notional*.01),0)
       sellfee=np.where(valid,np.minimum(np.maximum(sellshares*.005,1),sellnotional*.01),0)
       fees=float((buyfee+sellfee+sellnotional*.0000206+np.minimum(sellshares*.000195,9.79)+(shares+sellshares)*.000003).sum())
       friction=float(notional.sum()*slip/1e4);count=int(valid.sum());assert notional.sum()+fees+friction<=equity+1e-6
      equity+=gross-fees-friction;feesum+=fees;slipsum+=friction;turns+=count;grosssum+=gross;path.append(equity)
     eq=np.array(path);ddown=(eq/np.maximum.accumulate(np.r_[capital,eq])[1:]-1).min()*100
     results.append(dict(strategy=strategy,period=period,mode=mode,capital=capital,slip_bps=slip,days=len(eq),net_dollars=equity-capital,cagr=((equity/capital)**(252/len(eq))-1)*100,dd_pct=ddown,fees=feesum,friction=slipsum,round_trips=turns,mean_dollars_per_stock_rt=(equity-capital)/turns))
pd.DataFrame(results).to_csv(P/'broker_replay.csv',index=False)
# 500 count-matched random baskets, frozen monthly selection, same eligible data.
rng=np.random.default_rng(20260913);dates=sorted(daily.entry.unique());sim=np.zeros((len(dates),500));dmap={v:i for i,v in enumerate(dates)}
ut=tr[tr.strategy=='Universe'].copy();ut['month']=ut.entry.dt.strftime('%Y-%m')
for month,s in sel.groupby('month'):
 names=s.symbol.tolist();n=int(s.top_decile.sum());g=ut[ut.month==month];days=sorted(daily[daily.entry.dt.strftime('%Y-%m')==month].entry.unique());mat=g.pivot(index='entry',columns='symbol',values='ret').reindex(index=days,columns=names).fillna(0).to_numpy()
 weights=np.zeros((len(names),500))
 for j in range(500):weights[rng.choice(len(names),size=n,replace=False),j]=1/n
 sim[[dmap[v] for v in days]]=mat@weights
random=[]
actual=daily[daily.strategy=='TopDecile'].set_index('entry').reindex(dates).gross.to_numpy()
for period,start in starts.items():
 mask=pd.DatetimeIndex(dates)>=pd.Timestamp(start);x=sim[mask];a=actual[mask];sims=np.expm1(np.log1p(x).mean(axis=0)*252)*100;ac=np.expm1(np.log1p(a).mean()*252)*100
 random.append(dict(period=period,actual_gross_cagr=ac,random_median=np.median(sims),random_p025=np.percentile(sims,2.5),random_p975=np.percentile(sims,97.5),fraction_random_ge_actual=(sims>=ac).mean(),simulations=500))
pd.DataFrame(random).to_csv(P/'random_controls.csv',index=False)
print(pd.DataFrame(random).to_string(index=False));print(pd.DataFrame(results).query('capital==100000 and slip_bps==3').to_string(index=False));print('DONE')
