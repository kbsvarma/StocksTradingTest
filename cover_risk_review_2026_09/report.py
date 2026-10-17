from pathlib import Path
import pandas as pd,numpy as np,json
P=Path(__file__).resolve().parent;O=P.parent/'cover_exhaustive_2026_09';counts=json.loads((P/'counts.json').read_text());D=pd.read_csv(P/'pair_details.csv');B=pd.read_csv(P/'pair_behavior.csv');F=pd.read_csv(P/'company_quality_audit.csv').set_index('ticker');A=pd.read_csv(O/'stock_price_audit.csv').set_index('symbol');exc=pd.read_csv(O/'ai_mag7_exclusions_v2.csv');Q=pd.read_parquet(O/'price_matrix.parquet');chunks=[];tables=[]
def pct(v):return f'{100*v:.1f}%' if pd.notna(v) else 'unavailable'
chunks.append('''# Risk-focused constant-rebalanced portfolio review

Daily 50/50 remains the trading rule. These are two new **risk categories**, replacing the prior all-stocks/non-AI categories. No weights, stop-losses or trading frequency were optimized. Neither list is an instruction to buy.

The lower-risk label describes measured historical drawdown plus current size/profitability proxies. It does not mean a guaranteed loss limit, a credit rating, or that two equities are a safe portfolio. Different sectors do not ensure opposite returns. The higher-risk list contains speculative candidates whose past return does not establish future upside.

## Scope and selection
''')
chunks.append(f'We reconsidered all **{counts["universe_pairs"]:,} eligible pairs across {counts["universe_stocks"]:,} stocks**, calculated complete pre-2026 common-history results for every pair, and requested current profiles for every eligible stock. Profiles had usable sector data for {counts["profiles_with_sector"]:,}; {counts["quality_proxy_stocks"]:,} passed the size/profitability proxy. These are the eligible portion of the original exhaustive local-data inventory; missing/delisted histories and previously failed liquidity/adjustment screens remain excluded. This is not every stock ever listed.\n')
chunks.append('''Lower-risk eligibility requires both companies to have current USD market cap >=$10bn, trailing USD revenue >=$1bn, positive net margin and ROE; non-financial companies also need positive operating margin and operating cash flow. Companies must be in different sectors, with 2022–25 daily log-return correlation <=0.50. We require at least eight years of continuous shared history through 2025 and at most a **30% drawdown over that entire period**, not only 2022–25. Missing/non-USD financial inputs fail this particular screen. This deliberately conservative comparison does omit some strong foreign issuers.

Both lists require positive strategy return AND an advantage over matched 50/50 buy-and-hold after 10bps costs in **each** discovery half (2022–23 and 2024–25). Lower-risk ranking divides the weakest of those four annual log-growth measurements by the absolute long-history drawdown. Higher-risk ranking uses the weakest measurement directly, with discovery drawdown above 30%. Full ranking and exclusion files are retained. We do not replace losing 2026 candidates with winners.

**This is retrospective exploratory research.** Current fundamentals were unavailable at the historical start and therefore are not a historical point-in-time eligibility test. 2026 has already been examined in earlier work; displaying it separately does not make it a new untouched holdout. No fresh statistical proof follows from changing the screen.

All headline returns assume 5bps per dollar bought or sold, fractional shares, adjusted daily closing prices, and entry/exit costs. They exclude tax and broker minimum fees. An actual close-based execution rule and corporate actions must be independently validated before funding.
''')
for name in ['LOWER_RISK','HIGHER_RISK']:
 T=pd.read_csv(P/(name+'_TOP10.csv'));out=[]
 for _,r in T.iterrows():
  pair=r.a+' / '+r.b
  cf=D[(D.pair==pair)&(D.period=='confirmation')&(D.strategy=='daily_50_50')&(D.cost_bps==5)]
  ch=D[(D.pair==pair)&(D.period=='confirmation')&(D.strategy=='hold_50_50')&(D.cost_bps==5)]
  co=B[(B.pair==pair)&(B.period=='discovery')]
  out.append({'Rank':int(r['rank']),'Pair':pair,'Sectors':r.a_sector+' / '+r.b_sector,'2022–25 CAGR':pct(r.discovery_cagr_5bps),'Hold CAGR':pct(r.discovery_hold_cagr_5bps),'Pre-2026 worst DD':pct(r.pre_maxdd_5bps),'2022–25 DD':pct(r.discovery_maxdd_5bps),'2026 return':pct(cf.total_return.iloc[0]) if len(cf) else 'incomplete','2026 hold':pct(ch.total_return.iloc[0]) if len(ch) else 'incomplete','AI/Mag7 overlap':'No' if r.non_ai else 'Yes','Correlation':round(r.discovery_correlation,2),'Both down days':pct(co.both_down_fraction.iloc[0]) if len(co) else 'n/a'})
 out=pd.DataFrame(out);out.to_csv(P/(name+'_READABLE.csv'),index=False);tables.append(out)
 chunks.append('\n## '+('Lower historical risk, established-company screen' if name=='LOWER_RISK' else 'Higher historical risk, stronger past rebalancing advantage')+'\n')
 chunks.append(out.to_markdown(index=False)+'\n')
 stocks=pd.concat([T.a,T.b]).value_counts();chunks.append('Repeated stocks: '+', '.join(f'{x} in {n} pairs' for x,n in stocks.items() if n>1)+'. These are candidate alternatives, not ten independent positions.\n')
 chunks.append('### Individual pair diagnostics\n')
 for _,r in T.iterrows():
  pair=r.a+' / '+r.b;z=D[(D.pair==pair)&(D.strategy=='daily_50_50')&(D.cost_bps==5)];pre=z[z.period=='pre2026'].iloc[0];cf=z[z.period=='confirmation'];crisis=z[z.period=='2020'];beh=B[(B.pair==pair)&(B.period=='discovery')].iloc[0]
  text=f'**{pair}:** {r.a_longName} / {r.b_longName}. Pre-2026 window {pre.start} to {pre.end}; worst drawdown {pct(pre.maxdd)}, peak {pre.dd_peak}, trough {pre.dd_trough}, recovery {pre.dd_recovery}. Longest underwater spell: {int(pre.longest_underwater_sessions)} trading sessions. '
  if len(crisis):text+=f'2020 standalone return {pct(crisis.total_return.iloc[0])}, drawdown {pct(crisis.maxdd.iloc[0])}. '
  if len(cf):text+=f'2026 standalone $1,000 becomes ${cf.terminal_1000.iloc[0]:,.0f}; drawdown {pct(cf.maxdd.iloc[0])}. '
  text+=f'Discovery correlation {beh.correlation:.2f}; maximum rolling 63-session correlation {beh.rolling63_corr_max:.2f}; both stocks fell on {pct(beh.both_down_fraction)} of days. '
  high=D[(D.pair==pair)&(D.period=='confirmation')&(D.cost_bps==50)&(D.strategy=='daily_50_50')]
  if len(high):text+=f'At 50bps costs, 2026 return is {pct(high.total_return.iloc[0])}. '
  chunks.append(text+'\n')
needed=sorted(set(pd.concat(tables).Pair.str.split(' / ').explode()));rows=[]
for s in needed:
 f=F.loc[s];a=A.loc[s];ratio=f.totalDebt/f.ebitda if pd.notna(f.ebitda) and f.ebitda>0 and f.sector!='Financial Services' else np.nan
 rows.append({'Ticker':s,'Company':f.longName,'Sector':f.sector,'Market cap USD bn':round(f.marketCap/1e9,1) if f.currency=='USD' else np.nan,'Net margin':pct(f.profitMargins),'Op cash flow reported bn':round(f.operatingCashflow/1e9,2) if pd.notna(f.operatingCashflow) else np.nan,'Financial currency':f.financialCurrency,'Debt/EBITDA':round(ratio,2) if pd.notna(ratio) else np.nan,'Quality proxy pass':bool(f.quality_proxy_pass),'Raw return discrepancies':int(a.raw_log_discrepancies_over_10pct),'Raw comparisons':int(a.raw_return_comparisons),'Adjustment flags full history':int(a.adjustment_discrepancies_over_50bps)})
pd.DataFrame(rows).to_csv(P/'shortlist_company_audit.csv',index=False)
chunks.append('## Company and data checks\n'+pd.DataFrame(rows).to_markdown(index=False)+'\n')
chunks.append('Profiles were retrieved from Yahoo Finance on 2026-09-13; raw JSON, retrieval timestamps, failed requests and all quality decisions are saved. Debt/EBITDA is a rough supplementary diagnostic, not a cross-sector credit score. Profitability is trailing and can reverse. Current profiles do not replace review of filings, competitive position or valuation.\n')
chunks.append('Raw-return discrepancy counts come from comparison with an existing local panel that can have different close conventions and incomplete segments. They are unresolved flags, not proven errors and not independent vendor clearance. Any material adjustment issue in the longer history also requires checking even if the discovery adjustment screen passed. Databento verification remains outstanding; no paid data or trades were ordered.\n')
chunks.append('## Threshold sensitivity\n'+pd.DataFrame([{'Maximum historical drawdown':pct(float(t)),'Eligible established-company pairs':n} for t,n in counts['drawdown_sensitivity'].items()]).to_markdown(index=False)+'\n')
chunks.append('These counts vary only the drawdown ceiling while retaining all other lower-risk filters. They are not additional lists selected using 2026 outcomes.\n')
chunks.append('## Reproduction and artifacts\nRun `fetch_profiles.py`, `prehistory.py`, `rank.py`, `details.py`, and `report.py` with the Python environment used for the original study. `all_pairs_risk_audit.parquet` retains every evaluated pair and its qualification decisions. `company_quality_audit.csv` retains every stock. `ledger_checks.csv` compares independent share-ledger results to the exhaustive engine. `pair_details.csv` contains 5/10/25/50bps costs, matched hold benchmarks and drawdown dates; `pair_curves.parquet` retains the curves.\n')
(P/'REPORT.md').write_text('\n'.join(chunks));print('REPORT written; pairs',sum(map(len,tables)))
