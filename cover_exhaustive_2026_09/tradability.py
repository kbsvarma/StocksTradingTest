from pathlib import Path
import pandas as pd,numpy as np,json,hashlib,sqlite3
P=Path(__file__).resolve().parent;R=P.parent
reg=pd.read_csv(P/'stock_coverage.csv');cal=pd.to_datetime(json.loads((P/'calendar.json').read_text()));events=pd.read_csv(P/'price_events.csv');ev=events[(events.date>='2022-01-01')&(events.date<='2025-12-31')&(abs(events.close_dividend_discrepancy)>.005)].groupby('symbol').size().to_dict()
con=sqlite3.connect(f'file:{R}/.claude/worktrees/compassionate-elion-819afc/sage/db/sage.sqlite?mode=ro',uri=True);db=pd.read_sql_query('select ticker,date,adj_close,volume from prices',con);con.close();db.ticker=db.ticker.replace({'BRK-B':'BRK.B','BF-B':'BF.B'})
rows=[]
for _,r in reg[reg.status=='testable'].iterrows():
 if r.source=='local_sqlite':
  z=db[db.ticker==r.symbol];d=z.assign(date=pd.to_datetime(z.date)).set_index('date').rename(columns={'adj_close':'Close','volume':'Volume'});d=d.reindex(cal)
 else:
  d=pd.read_parquet(r.path);d.index=pd.to_datetime(d.index.date);d=d.reindex(cal)
 row={'symbol':r.symbol,'discovery_complete':bool(r.discovery_complete),'adjustment_flags_discovery':ev.get(r.symbol,0)}
 reasons=[]
 for h,(s,e) in enumerate([('2022-01-01','2023-12-31'),('2024-01-01','2025-12-31')],1):
  z=d.loc[s:e];dv=z.Close*z.Volume
  row[f'half{h}_median_dollar_volume']=float(dv.median());row[f'half{h}_p10_dollar_volume']=float(dv.quantile(.1));row[f'half{h}_zero_or_missing_volume_days']=int((z.Volume.isna()|(z.Volume<=0)).sum())
  if not np.isfinite(dv.median()) or dv.median()<5e6:reasons.append(f'half{h}_median_dollar_volume_below_5m')
  if not np.isfinite(dv.quantile(.1)) or dv.quantile(.1)<1e6:reasons.append(f'half{h}_p10_dollar_volume_below_1m')
  if row[f'half{h}_zero_or_missing_volume_days']>0:reasons.append(f'half{h}_nonpositive_or_missing_volume')
 if ev.get(r.symbol,0)>0:reasons.append('pending_material_adjustment_check')
 if not r.discovery_complete:reasons.append('incomplete_discovery')
 row['eligible']=not reasons;row['reason']=';'.join(reasons);rows.append(row)
a=pd.DataFrame(rows);a.to_csv(P/'tradability_stock_audit.csv',index=False)
f=pd.read_parquet(P/'all_ranked_pairs.parquet');ok=set(a[a.eligible].symbol);filtered=f[f.a.isin(ok)&f.b.isin(ok)].copy()
ex=set(pd.read_csv(P/'ai_mag7_exclusions_v2.csv').symbol);filtered['non_ai']=~filtered.a.isin(ex)&~filtered.b.isin(ex)
selected=[];counts={}
for label,d in [('all_stocks',filtered),('without_ai_mag7',filtered[filtered.non_ai])]:
 top=d.head(10).copy();top.insert(0,'list',label);top.insert(1,'rank',np.arange(1,len(top)+1));top['pair']=top.a+' / '+top.b
 top['confirmation_return_5bps']=np.expm1(np.log1p(top.confirmation_cagr_5bps)*top.confirmation_years);top['confirmation_hold_return_5bps']=np.expm1(np.log1p(top.confirmation_hold_cagr_5bps)*top.confirmation_years)
 top['confirmation_end']=pd.Timestamp('2025-12-31')+pd.to_timedelta((top.confirmation_years*365.25).round(),unit='D');top['confirmation_complete']=top.confirmation_end==cal[-1]
 top['confirmation_basic_pass']=top.confirmation_complete&(top.confirmation_return_5bps>0)&(top.confirmation_relative_log_5bps>0)&(top.confirmation_relative_log_10bps>0)
 top.to_csv(P/f'screened_top10_{label}.csv',index=False);selected.append(top)
 complete=(pd.Timestamp('2025-12-31')+pd.to_timedelta((d.confirmation_years*365.25).round(),unit='D'))==cal[-1]
 base=complete&(d.confirmation_cagr_5bps>0)&(d.confirmation_relative_log_5bps>0)&(d.confirmation_relative_log_10bps>0)
 counts[label]=dict(eligible_stocks=len(ok) if label=='all_stocks' else len(ok-set(pd.read_csv(P/'ai_mag7_exclusions_v2.csv').symbol)),eligible_pairs=len(d),positive_score=int((d.score>0).sum()),confirmation_complete_pairs=int(complete.sum()),confirmation_basic_pass_all_pairs=int(base.sum()),top10_basic_passes=int(top.confirmation_basic_pass.sum()))
 print(label);print(top[['rank','pair','score','discovery_cagr_5bps','discovery_hold_cagr_5bps','confirmation_return_5bps','confirmation_hold_return_5bps','confirmation_basic_pass']].to_string(index=False))
pd.concat(selected,ignore_index=True).to_csv(P/'screened_two_lists.csv',index=False);filtered.to_parquet(P/'all_screened_ranked_pairs.parquet',index=False)
counts['amendment_sha256']=hashlib.sha256((P/'TRADABILITY_AMENDMENT.md').read_bytes()).hexdigest();(P/'screened_counts.json').write_text(json.dumps(counts,indent=2))
print('Eligible stocks',len(ok),'eligible pairs',len(filtered),flush=True)
