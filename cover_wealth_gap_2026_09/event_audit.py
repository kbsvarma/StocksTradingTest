from pathlib import Path
import pandas as pd,numpy as np
P=Path(__file__).resolve().parent;q=pd.read_parquet(P.parent/'cover_validation_2026_09/long_eligible_prices.parquet').loc['2004-09-13':];pool=pd.read_csv(P/'verification_pool.csv');rows=[];events=[]
for _,p in pool.iterrows():
 a=q[[p.a,p.b]].dropna();g=a/a.shift();bonus=np.log(g.mean(axis=1))-.5*np.log(g).sum(axis=1);bonus.iloc[0]=0;top=bonus.nlargest(10);rows.append(dict(a=p.a,b=p.b,total_gross_diversification_log=bonus.sum(),top10_fraction=top.sum()/bonus.sum(),largest_date=str(top.index[0].date()),largest_bonus=top.iloc[0]))
 for date,value in top.items():events.append(dict(a=p.a,b=p.b,date=str(date.date()),gross_a=g.loc[date,p.a],gross_b=g.loc[date,p.b],gross_bonus=value))
pd.DataFrame(rows).to_csv(P/'event_concentration.csv',index=False);pd.DataFrame(events).to_csv(P/'largest_dispersion_days.csv',index=False)
