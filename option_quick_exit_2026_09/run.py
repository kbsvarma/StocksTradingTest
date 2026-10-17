from pathlib import Path
import pandas as pd,numpy as np,json,sys
BASE=Path(__file__).resolve().parent;R=BASE.parent;SYM=sys.argv[1] if len(sys.argv)>1 else 'IWM';P=BASE/SYM;P.mkdir(exist_ok=True)
u=pd.read_parquet(R/f'trend_replication_2026_07/data/etf/{SYM}_1m.parquet');et=u.index.tz_convert('America/New_York');u['day']=et.strftime('%Y-%m-%d');u['minute']=et.hour*60+et.minute
under={m:u[u.minute==m].set_index('day')[c] for m,c in [(570,'open'),(574,'close'),(584,'close')]}
rows=[];audit=[]
files=sorted((R/'spx_spread_bot/backtests/multi_ic_2026/data/iwm_wide').glob('*.parquet')) if SYM=='IWM' else sorted((R/'spx_spread_bot/backtests/narrow_width_databento_2026_05_21/spy_c_cbbo_1m').glob('*.parquet'))
for i,f in enumerate(files):
 day=str(pd.Timestamp(f.stem[-8:]).date())
 if day not in under[570].index:audit.append(dict(day=day,status='no_underlying'));continue
 q=pd.read_parquet(f)
 if SYM=='SPY':
  other=f.parent.parent/'spy_p_cbbo_1m'/f.name.replace('spy_c_','spy_p_')
  if not other.exists():continue
  q=pd.concat([q,pd.read_parquet(other)])
 et=q.index.tz_convert('America/New_York');q['minute']=et.hour*60+et.minute;q=q[q.minute.between(575,705)].copy();p=q.symbol.str.extract(rf'^{SYM}\s+(\d{{6}})([CP])(\d{{8}})$');q['expiry']=pd.to_datetime(p[0],format='%y%m%d',errors='coerce');q['right']=p[1];q['strike']=pd.to_numeric(p[2],errors='coerce')/1000;q['valid']=q.bid_px_00.ge(0)&q.ask_px_00.ge(q.bid_px_00)&q.ask_px_00.gt(0)&q.bid_sz_00.ge(1)&q.ask_sz_00.ge(1)
 for entry in [575,585]:
  if day not in under[entry-1].index:continue
  spot=float(under[entry-1].loc[day]);up=spot>=float(under[570].loc[day]);e=q[(q.minute==entry)&q.valid].drop_duplicates('symbol',keep='last');dte=(e.expiry-pd.Timestamp(day)).dt.days
  for tenor in ['0dte','1to7dte']:
   ee=e[dte.eq(0) if tenor=='0dte' else dte.between(1,7)].copy()
   if ee.empty:audit.append(dict(day=day,entry=entry,tenor=tenor,status='no_entry_chain'));continue
   expiry=ee.expiry.min();ee=ee[ee.expiry==expiry];ca=ee[ee.right=='C'].set_index('strike');pu=ee[ee.right=='P'].set_index('strike');common=ca.index.intersection(pu.index)
   if len(common)==0:audit.append(dict(day=day,entry=entry,tenor=tenor,status='no_common_strike'));continue
   k=min(common,key=lambda x:abs(x-spot))
   if abs(k-spot)>.51:audit.append(dict(day=day,entry=entry,tenor=tenor,status='missing_atm_coverage'));continue
   call=ca.loc[k,'symbol'];put=pu.loc[k,'symbol'];designs={'straddle':[call,put],'opening_follow':[call if up else put],'opening_fade':[put if up else call],'call_control':[call],'put_control':[put]};high=sorted(ca.index[ca.index>k]);low=sorted(pu.index[pu.index<k]);
   if high and low and high[0]-k<=1.01 and k-low[-1]<=1.01:designs['strangle']=[ca.loc[high[0],'symbol'],pu.loc[low[-1],'symbol']]
   for design,legs in designs.items():
    en=ee.set_index('symbol').loc[legs];premium=float(en.ask_px_00.sum()*100);n=len(legs);selected=q[q.symbol.isin(legs)&q.valid&(q.minute>entry)];piv=selected.drop_duplicates(['minute','symbol'],keep='last').pivot(index='minute',columns='symbol',values='bid_px_00').reindex(columns=legs).dropna();mins=piv.index.to_numpy();values=piv.sum(axis=1).to_numpy()*100
    for case in ['base','stress']:
     fee=n*2*(.65 if case=='base' else 1.30);slip=n*2*(0 if case=='base' else 1.);mark=values-premium-fee-slip
     for deadline in [630,690]:
      policies=[(target,stop) for target in [10,15] for stop in [20,40,None]]+[(None,None)]
      for target,stop in policies:
       reason='time';trigger=None;fillidx=None
       if target is not None:
        hits=np.where((mins<deadline)&((mark>=target)|((mark<=-stop) if stop is not None else False)))[0]
        if len(hits):
         j=hits[0];trigger=int(mins[j]);reason='target' if mark[j]>=target else 'stop'
         if j+1<len(mins) and mins[j+1]<=deadline+15:fillidx=j+1
       if fillidx is None:
        ti=np.where((mins>=deadline)&(mins<=deadline+15))[0]
        if len(ti):fillidx=ti[0]
       missing=fillidx is None;value=0. if missing else values[fillidx];net=value-premium-fee-slip;fillminute=None if missing else int(mins[fillidx]);policy='time_only' if target is None else f'tp{target}_sl{stop or "none"}'
       rows.append(dict(day=day,design=design,entry=entry,tenor=tenor,deadline=deadline,policy=policy,case=case,target=target,stop=stop,net=net,gross=value-premium,fees=fee,slippage=slip,premium=premium,reason=reason,trigger=trigger,exit_minute=fillminute,missing_exit=missing,legs='|'.join(legs),expiry=str(expiry.date()),strike=k,spot=spot))
   audit.append(dict(day=day,entry=entry,tenor=tenor,status='evaluated'))
 if i%100==0:print(i,'files',len(rows),'replays',flush=True)
a=pd.DataFrame(rows);a.to_parquet(P/'trades.parquet');pd.DataFrame(audit).to_csv(P/'coverage.csv',index=False)
keys=['design','entry','tenor','deadline','policy','case'];scores=[]
for key,g in a.groupby(keys):
 for period,lo,hi in [('development','2021','2023'),('validation','2023','2025'),('final','2025','2027'),('all','2021','2027')]:
  t=g[(g.day>=lo)&(g.day<hi)].sort_values('day');x=t.net
  if len(t)==0:continue
  wins=x[x>0];loss=x[x<0];cum=x.cumsum();scores.append(dict(zip(keys,key))|dict(period=period,n=len(x),net=x.sum(),mean=x.mean(),win_rate=(x>0).mean(),target_rate=(t.reason=='target').mean(),stop_rate=(t.reason=='stop').mean(),avg_win=wins.mean(),avg_loss=loss.mean(),worst=x.min(),dd=(cum-cum.cummax().clip(lower=0)).min(),profit_factor=wins.sum()/-loss.sum() if len(loss) else np.nan,median_premium=t.premium.median(),missing_exit=t.missing_exit.sum()))
pd.DataFrame(scores).to_csv(P/'scores.csv',index=False);print('COMPLETE',len(a),flush=True)
