from pathlib import Path
import pandas as pd,numpy as np,json
P=Path(__file__).resolve().parent
D=pd.read_parquet(P/'shortlist_daily.parquet');Q=pd.read_parquet(P/'price_matrix.parquet');holdings=['META','GOOGL','MSFT','NVDA','AMD','AVGO','AMZN','MU','DELL','INTC','TSM'];proxy=Q[holdings].pct_change(fill_method=None).mean(axis=1)
spy=pd.read_parquet(P.parent/'stock_overnight_2026_09/data/SPY.parquet');spy.index=pd.to_datetime(spy.index.date);spyr=spy['Adj Close'].pct_change(fill_method=None)
rows=[]
for period in ['discovery','confirmation']:
 d=D[(D.period==period)&(D.strategy=='daily_50_50')&(D.cost_bps==5)].pivot(index='date',columns='pair',values='return_net');pairs=list(d.columns)
 d['holdings_equal_weight_proxy']=proxy.reindex(d.index);d['SPY']=spyr.reindex(d.index)
 corr=d.corr();corr.to_csv(P/f'{period}_correlations.csv')
 for name in pairs:rows.append(dict(pair=name,period=period,holdings_proxy_correlation=corr.loc[name,'holdings_equal_weight_proxy'],spy_correlation=corr.loc[name,'SPY']))
pd.DataFrame(rows).to_csv(P/'shortlist_exposure.csv',index=False)
T=pd.read_csv(P/'screened_two_lists.csv');con=[]
for name,g in T.groupby('list'):
 counts=pd.concat([g.a,g.b]).value_counts()
 for n,count in counts.items():con.append(dict(list=name,symbol=n,pairs=int(count),fraction_of_pair_slots=float(count/(2*len(g)))))
pd.DataFrame(con).to_csv(P/'shortlist_concentration.csv',index=False)
