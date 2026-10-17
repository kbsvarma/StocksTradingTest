from pathlib import Path
import pandas as pd,numpy as np
p=Path(__file__).resolve().parent;t=pd.read_parquet(p/'trades.parquet');t=t[(t.symbol=='MES')&(t.window=='post_close')&(t.gate=='all')&(t.case=='base')].copy();a=pd.read_parquet(p.parent/'session_edge_2026_09/data/MES.parquet');vals=[]
for day in t.day:
 end=pd.Timestamp(day+' 15:55',tz='America/New_York').tz_convert('UTC');start=end-pd.Timedelta(days=1)
 vals.append((a.at[end,'open']-a.at[start,'open'])*5-5.5 if start in a.index and end in a.index else np.nan)
t['benchmark']=vals;t.to_parquet(p/'MES_comparison.parquet');out=[];rng=np.random.default_rng(608)
for period,lo,hi in [('development','2019','2023'),('validation','2023','2025'),('recent','2025','2027')]:
 g=t[(t.day>=lo)&(t.day<hi)];m=g.groupby(g.day.str[:7]).net.agg(['sum','count']);j=rng.integers(len(m),size=(5000,len(m)));boot=m['sum'].to_numpy()[j].sum(1)/m['count'].to_numpy()[j].sum(1);matched=g.dropna(subset='benchmark');out.append(dict(period=period,n=len(g),net=g.net.sum(),without_best5=g.net.sum()-g.net.nlargest(5).sum(),ci_low=np.quantile(boot,.025),ci_high=np.quantile(boot,.975),matched_n=len(matched),matched_overnight=matched.net.sum(),matched_closetoclose=matched.benchmark.sum(),worst_mae=g.mae.min()))
pd.DataFrame(out).to_csv(p/'MES_assessment.csv',index=False)
