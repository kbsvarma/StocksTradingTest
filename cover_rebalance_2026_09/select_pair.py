from pathlib import Path
import json,hashlib,itertools
import numpy as np
import pandas as pd
P=Path(__file__).resolve().parent;R=P.parent;DATA=R/'stock_overnight_2026_09/data'
CUTOFF=pd.Timestamp('2018-05-31');START=pd.Timestamp('2017-01-01')
def read_training(symbol):
 path=DATA/f'{dict(FB="META").get(symbol,symbol)}.parquet'
 if not path.exists():return None,path
 d=pd.read_parquet(path);d.index=pd.to_datetime(d.index.date)
 return d.loc[START:CUTOFF].copy(),path
u=pd.read_parquet(R/'opening_leader_2026_07/data/universe.parquet');month=u.month.min();assert month=='2018-06'
pit=pd.read_parquet(R/'volatile_direction_2026_07/data/pit_panel_2018_2026.parquet',columns=['raw_symbol','day','security_type'])
pit['day']=pd.to_datetime(pit.day);pit=pit[pit.day<=CUTOFF].sort_values('day').drop_duplicates('raw_symbol',keep='last').set_index('raw_symbol')
spy,_=read_training('SPY');cal=spy.index
rows=[];returns={};sources={}
for symbol in sorted(u[u.month==month].symbol.unique()):
 row={'symbol':symbol};rows.append(row)
 if symbol not in pit.index or pit.loc[symbol,'security_type']!='C':row['status']='not_archived_common_stock';continue
 d,path=read_training(symbol)
 if d is None or d.empty:row['status']='missing_training_history';continue
 if d.index.duplicated().any():row['status']='duplicate_dates';continue
 if 'Adj Close' not in d:row['status']='missing_adjusted_close';continue
 q=d['Adj Close'].reindex(cal);q=q.where(np.isfinite(q)&(q>0));valid=q.dropna()
 if valid.empty or CUTOFF not in valid.index:row['status']='missing_selection_close';continue
 log=np.log(q/q.shift());log=log.replace([np.inf,-np.inf],np.nan)
 active=q.loc[valid.index[0]:];coverage=float(active.notna().mean());n=int(log.notna().sum())
 row.update(training_returns=n,training_coverage=coverage,first=str(valid.index[0].date()),vol=float(log.std()*np.sqrt(252)))
 if n<252 or coverage<.98:row['status']='insufficient_training_data';continue
 row['status']='eligible';returns[symbol]=log;sources[symbol]=str(path)
audit=pd.DataFrame(rows);eligible=audit[audit.status=='eligible'].copy();cutoff=float(eligible.vol.quantile(.75));high=sorted(eligible[eligible.vol>=cutoff].symbol)
pairs=[]
for a,b in itertools.combinations(high,2):
 d=pd.concat([returns[a],returns[b]],axis=1,keys=[a,b]).dropna()
 if len(d)<252:continue
 rho=float(d[a].corr(d[b]));score=float((d[a]-d[b]).var()*252/8)
 pairs.append(dict(a=a,b=b,paired_returns=len(d),vol_a=float(d[a].std()*np.sqrt(252)),vol_b=float(d[b].std()*np.sqrt(252)),correlation=rho,diversification_proxy=score,qualifies=rho<=.20))
pairs=pd.DataFrame(pairs).sort_values(['diversification_proxy','a','b'],ascending=[False,True,True]);pairs.to_csv(P/'pair_training_rankings.csv',index=False);audit.to_csv(P/'selection_universe_audit.csv',index=False)
qual=pairs[pairs.qualifies]
summary={'selection_cutoff':str(CUTOFF.date()),'universe_month':month,'universe_stocks':len(audit),'eligible_stocks':len(eligible),'high_vol_stocks':len(high),'vol_top_quartile_cutoff':cutoff,'qualifying_pairs':len(qual),'exclusion_counts':audit.status.value_counts().to_dict(),'protocol_sha256':hashlib.sha256((P/'PROTOCOL.md').read_bytes()).hexdigest()}
if qual.empty:summary['status']='no_qualifying_pair'
else:
 summary.update(status='selected',pair=qual.iloc[0].drop('qualifies').to_dict());summary['source_paths']={s:sources[s] for s in [summary['pair']['a'],summary['pair']['b']]};summary['source_sha256']={s:hashlib.sha256(Path(path).read_bytes()).hexdigest() for s,path in summary['source_paths'].items()}
(P/'selected_pair.json').write_text(json.dumps(summary,indent=2));print(json.dumps(summary,indent=2))
