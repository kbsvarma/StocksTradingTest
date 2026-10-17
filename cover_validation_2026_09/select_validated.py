from pathlib import Path
import pandas as pd,numpy as np,json
P=Path(__file__).resolve().parent;O=P.parent/'cover_exhaustive_2026_09'
ex=pd.read_csv(O/'ai_mag7_exclusions_v2.csv');ex=pd.concat([ex,pd.DataFrame([{'symbol':'KEEL','reason':'BITF successor; issuer describes pure-play AI infrastructure'}])]).drop_duplicates('symbol');ex.to_csv(P/'ai_exclusions_final.csv',index=False);excluded=set(ex.symbol)
long=pd.read_csv(P/'long_period_checks.csv');roll=pd.read_csv(P/'pool_long_rolling.csv');price=pd.read_csv(P/'pair_price_verification.csv');exe=pd.read_csv(P/'execution_results.csv');rows=pd.read_parquet(P/'long_lower_risk_ranked.parquet').copy()
for cost in [10,25,50]:
 z=long[(long.period=='longest')&(long.cost_bps==cost)].drop(columns=['period','cost_bps']);rows=rows.merge(z.rename(columns={c:f'long_{cost}_'+c for c in z if c not in ['a','b']}),on=['a','b'],how='left')
for horizon in [2,5]:
 z=roll[roll.horizon_years==horizon].drop(columns='horizon_years');rows=rows.merge(z.rename(columns={c:f'roll_{horizon}_'+c for c in z if c not in ['a','b']}),on=['a','b'],how='left')
for period in ['discovery','confirmation']:
 z=price[price.period==period].drop(columns='period');rows=rows.merge(z.rename(columns={c:'price_'+period+'_'+c for c in z if c not in ['a','b']}),on=['a','b'],how='left')
for period in ['vendor_full','discovery','confirmation']:
 z=exe[(exe.period==period)&(exe.extra_bps==10)&(exe.dividend_lag_days==30)].drop(columns=['period','extra_bps','dividend_lag_days']);rows=rows.merge(z.rename(columns={c:'exec_'+period+'_'+c for c in z if c not in ['a','b']}),on=['a','b'],how='left')
rows['outside_excluded_universe']=~rows.a.isin(excluded)&~rows.b.isin(excluded)
# These are explicit post-screen decision hurdles, not a claim of preregistration.
rows['material_long_edge']=rows.long_10_annual_log_advantage>=.01
rows['cost_resilience']=rows.long_25_annual_log_advantage>0
rows['full_drawdown_limit']=rows.long_10_maxdd>=-.30
rows['entry_date_resilience']=(rows.roll_2_beat_hold_fraction>=.75)&(rows.roll_5_beat_hold_fraction>=.75)
rows['price_verified']=(rows.price_discovery_vendor_missing_a==0)&(rows.price_discovery_vendor_missing_b==0)&(rows.price_discovery_terminal_relative_difference.abs()<.005)
rows['execution_edge']=True;rows['execution_observations_usable']=True
for period in ['vendor_full','discovery','confirmation']:
 rows['execution_edge'] &= rows['exec_'+period+'_annual_log_advantage']>0
 rows['execution_observations_usable'] &= rows['exec_'+period+'_status'].fillna('').str.startswith('pass')
checks=['material_long_edge','cost_resilience','full_drawdown_limit','entry_date_resilience','price_verified','execution_edge','execution_observations_usable'];rows['research_trial_candidate']=rows[checks].all(axis=1);rows['failed_checks']=rows.apply(lambda r:';'.join(k for k in checks if not r[k]),axis=1);rows.to_csv(P/'LOWER_VALIDATION_ALL.csv',index=False)
for name,mask in [('ALL',rows.research_trial_candidate),('NON_AI',rows.research_trial_candidate&rows.outside_excluded_universe)]:
 z=rows[mask].sort_values(['lower_risk_score','a','b'],ascending=[False,True,True]);z.to_csv(P/('VALIDATED_LOWER_'+name+'.csv'),index=False);z.head(10).to_csv(P/('VALIDATED_LOWER_TOP10_'+name+'.csv'),index=False);print(name,len(z));print(z.head(10)[['a','b','long_10_cagr','long_10_hold_cagr','long_10_maxdd','exec_discovery_cagr','exec_discovery_hold_cagr','failed_checks']].to_string(index=False))
(P/'DECISION_FILTER_COUNTS.json').write_text(json.dumps({k:int(rows[k].sum()) for k in checks+['research_trial_candidate']},indent=2))
