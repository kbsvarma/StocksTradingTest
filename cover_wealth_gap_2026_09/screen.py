from pathlib import Path
import pandas as pd,numpy as np,json
P=Path(__file__).resolve().parent;V=P.parent/'cover_validation_2026_09';profile=pd.read_csv(V/'company_quality_audit.csv').set_index('ticker');ex=pd.read_csv(V/'ai_exclusions_final.csv');print('exclusion fields',ex.columns.tolist(),flush=True);excluded=set(ex.iloc[:,0])
cols=['a','b','start_index','end_index','full_years','full_cagr_10bps','full_a_cagr_5bps','full_b_cagr_5bps','full_hold_cagr_5bps','full_maxdd_5bps','full_correlation','full_annual_turnover']
x=pd.concat([pd.read_parquet(f,columns=cols) for f in sorted((V/'long_pairs').glob('part_*.parquet'))],ignore_index=True)
x=x[x.a.map(profile.company_stock).fillna(False)&x.b.map(profile.company_stock).fillna(False)&x.a.map(profile.longName).ne(x.b.map(profile.longName))].copy()
x['no_ai']=~x.a.isin(excluded)&~x.b.isin(excluded);factor=(.999/1.001)/(.9995/1.0005)
for name,col in [('rebalance','full_cagr_10bps'),('stock_a','full_a_cagr_5bps'),('stock_b','full_b_cagr_5bps'),('hold','full_hold_cagr_5bps')]:x[name+'_multiple']=np.exp(np.log1p(x[col])*x.full_years)*(1 if name=='rebalance' else factor)
x['best_stock_multiple']=x[['stock_a_multiple','stock_b_multiple']].max(axis=1);x['over_best']=x.rebalance_multiple/x.best_stock_multiple;x['over_hold']=x.rebalance_multiple/x.hold_multiple
q=pd.read_parquet(V/'long_eligible_prices.parquet');dates=q.index
for col,out in [('start_index','start'),('end_index','end')]:x[out]=x[col].map(lambda z:str(dates[int(z)].date()) if pd.notna(z) else None)
x['current_end']=x.end.eq(str(dates[-1].date()));x=x.sort_values('over_best',ascending=False);x.to_parquet(P/'all_pairs_wealth_comparison.parquet',index=False)
z=x[x.full_years.ge(8)&x.rebalance_multiple.ge(1)&x.current_end]
z.to_parquet(P/'eight_year_candidates.parquet',index=False)
for name,a in [('all',z),('non_ai',z[z.no_ai]),('under30dd',z[z.full_maxdd_5bps.ge(-.3)]),('under40dd',z[z.full_maxdd_5bps.ge(-.4)])]:
 a.head(100).to_csv(P/f'top100_{name}.csv',index=False);print(name,len(a),flush=True);print(a.head(12)[['a','b','full_years','stock_a_multiple','stock_b_multiple','rebalance_multiple','over_best','over_hold','full_maxdd_5bps']].to_string(index=False),flush=True)
counts={'all_pairs':len(x),'eight_year_current_pairs':len(z),'at_least_8x_best':int(z.over_best.ge(8).sum()),'at_least_4x_best':int(z.over_best.ge(4).sum()),'at_least_2x_best':int(z.over_best.ge(2).sum()),'8x_best_under30dd':int((z.over_best.ge(8)&z.full_maxdd_5bps.ge(-.3)).sum())};(P/'counts.json').write_text(json.dumps(counts,indent=2));print(counts,flush=True)
