from pathlib import Path
import pandas as pd,numpy as np,json
P=Path(__file__).resolve().parent;O=P.parent/'cover_exhaustive_2026_09';Q=pd.read_parquet(O/'price_matrix.parquet');old=pd.read_csv(O/'tradability_stock_audit.csv').set_index('symbol');reg=pd.read_csv(O/'stock_coverage.csv').set_index('symbol');manifest=json.loads((P/'expanded_sources.json').read_text());rows=[];columns={};new_ok=[]
for s,r in manifest.items():
 row={'symbol':s,'source_status':r['status'],'eligible':False,'reason':r['status']}
 if '#' in s:
  row['reason']='identity_mismatch_fragment_symbol';rows.append(row);continue
 if r['status']=='ok':
  try:
   d=pd.read_parquet(P/'expanded_daily'/f'{s}.parquet');d.index=pd.to_datetime(d.index.date);d=d[~d.index.duplicated(keep='last')].reindex(Q.index);q=d['Adj Close'];disc=d.loc['2021-12-31':'2025-12-31'];row['discovery_complete']=bool(disc['Adj Close'].notna().all() and (disc['Adj Close']>0).all());row['adjustment_flags']=int(((q/q.shift()-(d.Close+d.Dividends)/d.Close.shift()).abs().loc['2022-01-01':'2025-12-31']>.005).sum());reasons=[]
   if not row['discovery_complete']:reasons.append('incomplete_discovery')
   if row['adjustment_flags']:reasons.append('adjustment_flags')
   for label,start,end in [('half1','2022-01-01','2023-12-31'),('half2','2024-01-01','2025-12-31')]:
    z=d.loc[start:end];dv=z.Close*z.Volume;row[label+'_median_dv']=float(dv.median());row[label+'_p10_dv']=float(dv.quantile(.1));row[label+'_missing_zero_volume']=int((z.Volume.isna()|z.Volume.le(0)).sum())
    if row[label+'_median_dv']<5e6 or row[label+'_p10_dv']<1e6 or row[label+'_missing_zero_volume']:reasons.append(label+'_liquidity')
   row['eligible']=len(reasons)==0;row['reason']=';'.join(reasons) if reasons else 'pass'
   if row['eligible']:columns[s]=q;new_ok.append(s)
  except Exception as e:row['reason']='parse_error:'+str(e)[:200]
 rows.append(row)
base=old.index[old.eligible].tolist();matrix=pd.concat([Q[base],pd.DataFrame(columns,index=Q.index)],axis=1);assert not matrix.columns.duplicated().any();matrix.to_parquet(P/'expanded_eligible_prices.parquet');pd.DataFrame(rows).to_csv(P/'expansion_eligibility.csv',index=False);meta={'original_eligible':len(base),'new_attempted':len(rows),'new_eligible':len(new_ok),'total_eligible':matrix.shape[1],'total_pairs':matrix.shape[1]*(matrix.shape[1]-1)//2,'new_eligible_symbols':new_ok};(P/'EXPANSION_COUNTS.json').write_text(json.dumps(meta,indent=2));print(meta,flush=True)
