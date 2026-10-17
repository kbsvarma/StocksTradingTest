from pathlib import Path
import pandas as pd,numpy as np,json
P=Path(__file__).resolve().parent;O=P.parent/'cover_exhaustive_2026_09'
F=pd.read_csv(P/'company_profiles.csv').set_index('ticker')
def positive(c):return pd.to_numeric(F[c],errors='coerce').gt(0)
F['size_pass']=(F.marketCap>=1e10)&(F.totalRevenue>=1e9)&F.currency.eq('USD')&F.financialCurrency.eq('USD')
F['profit_pass']=positive('profitMargins')&positive('returnOnEquity')
F['cash_pass']=F.sector.eq('Financial Services')|(positive('operatingCashflow')&positive('operatingMargins'))
F['quality_proxy_pass']=F.size_pass&F.profit_pass&F.cash_pass&F.status.eq('ok')
F.to_csv(P/'company_quality_audit.csv')
D=pd.read_parquet(O/'all_screened_ranked_pairs.parquet');R=pd.read_parquet(P/'pre2026_all_pairs.parquet');assert D[['a','b']].equals(R[['a','b']]);D=pd.concat([D.reset_index(drop=True),R.drop(columns=['a','b']).reset_index(drop=True)],axis=1)
for side in ['a','b']:
 for col in ['longName','sector','industry','marketCap','profitMargins','quality_proxy_pass']:
  D[side+'_'+col]=D[side].map(F[col])
D['cross_sector']=D.a_sector.notna()&D.b_sector.notna()&D.a_sector.ne(D.b_sector)
D['robust_score_10bps']=np.minimum.reduce([np.log1p(D.half1_cagr_10bps),np.log1p(D.half2_cagr_10bps),D.half1_relative_log_10bps,D.half2_relative_log_10bps])
D['positive_halves']=D.robust_score_10bps>0
quality=D.a_quality_proxy_pass.fillna(False)&D.b_quality_proxy_pass.fillna(False)
base=quality&D.cross_sector&(D.discovery_correlation<=.5)&(D.pre_years>=8)&D.positive_halves
D['lower_risk_eligible']=base&(D.pre_maxdd_5bps>=-.30)&(D.discovery_maxdd_5bps>=-.30)
D['lower_risk_score']=D.robust_score_10bps/(-D.pre_maxdd_5bps)
D['speculative_eligible']=D.positive_halves&(D.discovery_maxdd_5bps<-.30)
D.to_parquet(P/'all_pairs_risk_audit.parquet',index=False)
L=D[D.lower_risk_eligible].sort_values(['lower_risk_score','discovery_cagr_5bps','a','b'],ascending=[False,False,True,True]).copy();H=D[D.speculative_eligible].sort_values(['robust_score_10bps','discovery_cagr_5bps','a','b'],ascending=[False,False,True,True]).copy()
L.to_parquet(P/'lower_risk_all_ranked.parquet',index=False);H.to_parquet(P/'higher_risk_all_ranked.parquet',index=False)
for name,df in [('LOWER_RISK',L),('HIGHER_RISK',H)]:
 z=df.head(10).copy();z.insert(0,'rank',range(1,len(z)+1));z.insert(0,'list',name);z.to_csv(P/(name+'_TOP10.csv'),index=False)
 print(name,len(df));print(z[['rank','a','b','a_sector','b_sector','discovery_cagr_5bps','discovery_hold_cagr_5bps','pre_maxdd_5bps','discovery_maxdd_5bps','confirmation_cagr_5bps']].to_string(index=False))
counts={'universe_stocks':len(F),'universe_pairs':len(D),'profiles_with_sector':int(F.status.eq('ok').sum()),'quality_proxy_stocks':int(F.quality_proxy_pass.sum()),'quality_pairs':int(quality.sum()),'quality_cross_sector_pairs':int((quality&D.cross_sector).sum()),'lower_base_before_drawdown':int(base.sum()),'lower_risk_eligible':len(L),'higher_risk_eligible':len(H),'drawdown_sensitivity':{str(t):int((base&(D.pre_maxdd_5bps>=-t)&(D.discovery_maxdd_5bps>=-t)).sum()) for t in [.2,.25,.3,.35,.4,.5]}}
(P/'counts.json').write_text(json.dumps(counts,indent=2));print(counts)
