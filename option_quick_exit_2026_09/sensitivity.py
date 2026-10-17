from pathlib import Path
import pandas as pd,numpy as np
P=Path(__file__).resolve().parent;R=P.parent
x=pd.read_parquet(P/'IWM/trades.parquet');x=x[(x.design=='straddle')&(x.entry==575)&(x.deadline==630)&x.policy.isin(['tp10_sl40','tp15_sl40'])].copy();x['same_snapshot_net']=x.net
for day,g in x.groupby('day'):
 active=g[g.trigger.notna()]
 if active.empty:continue
 f=R/'spx_spread_bot/backtests/multi_ic_2026/data/iwm_wide'/('iwm_cbbo_'+day.replace('-','')+'.parquet');q=pd.read_parquet(f);et=q.index.tz_convert('America/New_York');q['minute']=et.hour*60+et.minute;q=q.drop_duplicates(['minute','symbol'],keep='last').set_index(['minute','symbol'])
 for i,t in active.iterrows():
  bids=sum(float(q.loc[(int(t.trigger),leg),'bid_px_00']) for leg in t.legs.split('|'));x.loc[i,'same_snapshot_net']=bids*100-t.premium-t.fees-t.slippage
x.to_parquet(P/'IWM/sensitivity.parquet');x['period']=np.where(x.day<'2023','development',np.where(x.day<'2025','validation','final'));x.groupby(['case','tenor','policy','period']).agg(n=('net','size'),mean_delayed=('net','mean'),mean_same_snapshot=('same_snapshot_net','mean')).to_csv(P/'IWM/sensitivity.csv');print('sensitivity complete')
