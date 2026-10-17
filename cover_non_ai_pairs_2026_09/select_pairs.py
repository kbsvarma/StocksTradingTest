from pathlib import Path
import json,hashlib,itertools
import numpy as np
import pandas as pd
from scipy.optimize import milp,Bounds,LinearConstraint
P=Path(__file__).resolve().parent;ROOT=P.parent;DATA=ROOT/'stock_overnight_2026_09/data'
GROUPS={
 'airlines':'AAL UAL',
 'pharmaceuticals':'ABBV AMGN BIIB BMY GILD JNJ MRK PFE',
 'medical_devices':'ALGN DXCM ISRG MDT',
 'aerospace':'BA',
 'banks':'BAC C WFC',
 'financial_services':'BRK.B GS MS',
 'cable_media':'CHTR CMCSA DIS',
 'retail':'COST DLTR HD LOW ORLY TGT ULTA WMT',
 'railroads':'CSX',
 'oil_gas':'CVX MPC XOM',
 'travel':'BKNG EXPE MAR',
 'automotive':'F GM',
 'consumer_apparel':'LULU NKE',
 'consumer_staples':'MDLZ PEP PG',
 'restaurants':'SBUX',
 'telecom':'T TMUS VZ',
 'health_insurance':'UNH',
 'vehicle_rental':'CAR',
}
ALLOWED={s:g for g,names in GROUPS.items() for s in names.split()}
EXPLICIT=set('META FB GOOG GOOGL MSFT NVDA AMD AVGO AMZN MU DELL INTC TSM TSMC AAPL TSLA'.split())
INFRA=set('ADI AMAT ASML CDNS CSCO EQIX FTNT KLAC LRCX MCHP MRVL NXPI ON QCOM SNPS SWKS TER TXN XLNX PANW CRWD'.split())
SPECULATIVE=set('AMC COIN MARA LCID NIO RIVN PLUG BNTX MRNA NVAX LLY PTON'.split())
for name in ALLOWED:assert name not in EXPLICIT|INFRA|SPECULATIVE
START=pd.Timestamp('2019-01-01');END=pd.Timestamp('2021-12-31')
def training(s):
 path=DATA/f'{dict(FB="META").get(s,s)}.parquet'
 if not path.exists():return None,path
 d=pd.read_parquet(path);d.index=pd.to_datetime(d.index.date)
 return d.loc[START:END].copy(),path
spy,_=training('SPY');cal=spy.index;cut=cal[-1]
u=pd.read_parquet(ROOT/'opening_leader_2026_07/data/universe.parquet');u=u[u.month=='2022-01']
pit=pd.read_parquet(ROOT/'volatile_direction_2026_07/data/pit_panel_2018_2026.parquet',columns=['raw_symbol','day','security_type']);pit.day=pd.to_datetime(pit.day);pit=pit[pit.day<=END].sort_values('day').drop_duplicates('raw_symbol',keep='last').set_index('raw_symbol')
rows=[];ret={};src={}
for symbol in sorted(u.symbol.unique()):
 row=dict(symbol=symbol,business_group=ALLOWED.get(symbol,''));rows.append(row)
 if symbol not in ALLOWED:
  row['status']='excluded_user_holdings_mag7' if symbol in EXPLICIT else 'excluded_ai_infrastructure' if symbol in INFRA else 'excluded_other_hype' if symbol in SPECULATIVE else 'outside_conservative_business_universe';continue
 if symbol not in pit.index or pit.loc[symbol,'security_type']!='C':row['status']='not_archived_common_stock';continue
 d,path=training(symbol)
 if d is None or d.empty:row['status']='missing_training_history';continue
 if d.index.duplicated().any():row['status']='duplicate_training_dates';continue
 d=d.reindex(cal);q=d['Adj Close'].where(lambda x:np.isfinite(x)&(x>0));r=np.log(q/q.shift()).replace([np.inf,-np.inf],np.nan)
 n=int(r.notna().sum());coverage=float(q.notna().mean());vol=float(r.std()*np.sqrt(252));years=(cal[-1]-cal[0]).days/365.25
 cagr=float((q.iloc[-1]/q.iloc[0])**(1/years)-1) if q.iloc[[0,-1]].notna().all() else np.nan
 efficiency=float(abs(r.sum())/r.abs().sum());liquidity=float((d.Close*d.Volume).median())
 row.update(returns=n,coverage=coverage,vol=vol,cagr=cagr,path_efficiency=efficiency,median_daily_dollar_volume=liquidity)
 failed=[]
 if n<700 or coverage<.99 or not np.isfinite(cagr):failed.append('coverage')
 if not np.isfinite(q.iloc[-1]):failed.append('entry_price')
 if liquidity<20e6:failed.append('liquidity')
 if vol<.25:failed.append('low_volatility')
 if not -.20<=cagr<=.30:failed.append('non_modest_training_trend')
 if efficiency>.10:failed.append('persistent_training_path')
 row['status']='eligible' if not failed else '+'.join(failed)
 if not failed:ret[symbol]=r;src[symbol]=path
pd.DataFrame([dict(symbol=s,business_group=g) for s,g in sorted(ALLOWED.items())]).to_csv(P/'allowed_businesses.csv',index=False)
audit=pd.DataFrame(rows);audit.to_csv(P/'universe_audit.csv',index=False)
pairs=[]
for a,b in itertools.combinations(sorted(ret),2):
 if ALLOWED[a]==ALLOWED[b]:continue
 rr=pd.concat([ret[a],ret[b]],axis=1,keys=[a,b]).dropna();rho=float(rr[a].corr(rr[b]));score=float((rr[a]-rr[b]).var()*252/8)
 if len(rr)>=700 and rho<=.35:pairs.append(dict(a=a,b=b,group_a=ALLOWED[a],group_b=ALLOWED[b],correlation=rho,score=score,training_returns=len(rr)))
pairs=pd.DataFrame(pairs).sort_values(['score','a','b'],ascending=[False,True,True]).reset_index(drop=True)
pairs.to_csv(P/'all_qualifying_training_pairs.csv',index=False)
if pairs.empty:raise RuntimeError('No qualifying pairs; filters unchanged')
names=sorted(ret);A=np.array([[float(s in [r.a,r.b]) for _,r in pairs.iterrows()] for s in names]);A=np.vstack([A,np.ones(len(pairs))])
low=np.r_[np.zeros(len(names)),5.];high=np.r_[np.ones(len(names)),5.]
result=milp(c=-pairs.score.to_numpy()+np.arange(len(pairs))*1e-12,integrality=np.ones(len(pairs)),bounds=Bounds(0,1),constraints=LinearConstraint(A,low,high),options={'time_limit':60})
if not result.success:raise RuntimeError('Cannot select five disjoint pairs under locked criteria: '+result.message)
chosen=pairs[result.x>.5].copy().sort_values(['score','a','b'],ascending=[False,True,True]);chosen['pair']=chosen.a+'_'+chosen.b;chosen.to_csv(P/'selected_pairs.csv',index=False)
selected_symbols=sorted(set(chosen.a)|set(chosen.b))
summary=dict(status='frozen',cutoff=str(cut.date()),training_start=str(cal[0].date()),evaluation_start='2022-01-03',pairs=chosen.to_dict('records'),excluded_counts=audit.status.value_counts().to_dict(),universe_size=len(u),eligible_stocks=len(ret),qualifying_pairs=len(pairs),source_paths={s:str(src[s]) for s in selected_symbols},source_sha256={s:hashlib.sha256(src[s].read_bytes()).hexdigest() for s in selected_symbols},protocol_sha256=hashlib.sha256((P/'PROTOCOL.md').read_bytes()).hexdigest())
(P/'selection.json').write_text(json.dumps(summary,indent=2));print(json.dumps(summary,indent=2));print(audit[audit.symbol.isin(selected_symbols)].to_string(index=False))
