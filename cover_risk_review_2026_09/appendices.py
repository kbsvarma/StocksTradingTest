from pathlib import Path
import pandas as pd,numpy as np
P=Path(__file__).resolve().parent
D=pd.read_parquet(P/'all_pairs_risk_audit.parquet');L=pd.read_parquet(P/'lower_risk_all_ranked.parquet')
def compact(x):
 rows=[]
 for _,r in x.iterrows():
  rows.append({'Pair':r.a+' / '+r.b,'Companies':str(r.a_longName)+' / '+str(r.b_longName),'2022–25 CAGR':f'{100*r.discovery_cagr_5bps:.1f}%','Hold CAGR':f'{100*r.discovery_hold_cagr_5bps:.1f}%','Pre-2026 worst DD':f'{100*r.pre_maxdd_5bps:.1f}%','2026 return':f'{100*np.expm1(np.log1p(r.confirmation_cagr_5bps)*r.confirmation_years):.1f}%'})
 return pd.DataFrame(rows)
noai=L[L.non_ai].head(10);noai.to_csv(P/'LOWER_RISK_NON_AI_REFERENCE_FULL.csv',index=False);table=compact(noai);table.to_csv(P/'LOWER_RISK_NON_AI_REFERENCE.csv',index=False)
pairs=[tuple(sorted(p)) for p in [('MSFT','PG'),('AAPL','KO'),('MSFT','JNJ'),('JNJ','KO'),('JNJ','PG'),('JPM','KO')]];control=pd.concat([D[(D.a==a)&(D.b==b)] for a,b in pairs]);control.to_csv(P/'illustrative_blue_chip_controls.csv',index=False);ct=compact(control);ct['Passes lower-risk rules']=control.lower_risk_eligible.to_numpy()
text='''# Supplementary comparisons

These tables do not replace or rerank the two requested risk lists. The first applies the previously documented AI/Mag7 exclusion to the entire lower-risk qualifying list. The second contains explicitly illustrative familiar blue-chip combinations, not an exhaustive alternative shortlist.

## Lower-risk candidates outside the AI/Mag7 exclusion list

'''+table.to_markdown(index=False)+'\n\n## Familiar blue-chip controls\n\n'+ct.to_markdown(index=False)+'''

Microsoft/Procter & Gamble and Microsoft/Johnson & Johnson qualify but rank below the leaders because their net historical rebalancing advantage is smaller. Johnson & Johnson/Procter & Gamble fails the positive-advantage requirement in the second discovery half; the other failing examples breach the 30% long-history drawdown ceiling and/or fail a half-period advantage check.

All returns are historical, pre-tax and after modeled 5bps costs. 2026 returns run from the 2025 year-end close through September 11, 2026 and were not used to rank. Full audit columns retain the qualification reasons.
'''
(P/'SUPPLEMENTARY_COMPARISONS.md').write_text(text);print(table.to_string(index=False));print(ct.to_string(index=False))
