from pathlib import Path
import pandas as pd,numpy as np,json
P=Path(__file__).resolve().parent
assert (P/'EXECUTION_COMPLETE').exists()
def table(d):return d.to_markdown(index=False,floatfmt='.2f')
primary=pd.read_csv(P/'primary_shortlist.csv');primary['pair']=primary.a+'/'+primary.b
p=primary[['pair','stock_a_multiple','stock_b_multiple','hold_multiple','rebalance_multiple','over_best','maxdd']].copy();p['maxdd']*=100;p.columns=['Pair','Stock A ×','Stock B ×','Hold pair ×','Daily 50/50 ×','Over better stock ×','Max drawdown %']
alt=pd.read_csv(P/'long_history_alternatives.csv');alt['pair']=alt.a+'/'+alt.b;alt['maxdd']*=100
cost=pd.read_csv(P/'cost_stresses.csv');cost['pair']=cost.a+'/'+cost.b;costtable=cost.pivot(index='pair',columns='scenario',values='rebalance_multiple').reset_index();costtable=costtable[['pair','fixed_10bp','fixed_25bp','fixed_100bp','one_cent_spread_floor_10bp','two_cent_spread_floor_10bp']].rename(columns={'pair':'Pair','fixed_10bp':'10 bp','fixed_25bp':'25 bp','fixed_100bp':'100 bp','one_cent_spread_floor_10bp':'One-cent floor','two_cent_spread_floor_10bp':'Two-cent floor'})
risk=pd.read_csv(P/'risk_frontier.csv');risk['pair']=risk.a+'/'+risk.b;risk['drawdown_cap']*=100;risk['maxdd']*=100
chron=pd.read_csv(P/'chronological_top10.csv');chron['pair']=chron.a+'/'+chron.b;chron['subsequent_maxdd']*=100
execs=pd.read_csv(P/'execution_results.csv');execs['pair']=execs.a+'/'+execs.b;chosen=execs[execs.dividend_lag_days.eq(30)&execs.extra_bps.eq(10)].copy();chosen['rebalance_multiple']=chosen.terminal_1000/1000;chosen['hold_multiple']=chosen.hold_terminal_1000/1000;chosen['over_hold']=chosen.terminal_1000/chosen.hold_terminal_1000;chosen['maxdd']*=100
chosen.to_csv(P/'execution_summary.csv',index=False)
e=chosen[chosen.pair.isin(primary.pair)].copy();e.to_csv(P/'primary_execution_summary.csv',index=False)
checks=json.loads((P/'verification.json').read_text());spend=json.loads((P/'SPEND_QUOTES.json').read_text());status=json.loads((P/'SPEND_STATUS.json').read_text());prior=spend['prior_actual_or_quote_including_uncertain'];quote_charge=spend.get('actual_cost_usd') or spend['quote_usd'];estimated=prior+quote_charge+status['quote_usd']
stat=execs.status.value_counts().rename_axis('status').reset_index(name='scenarios')
text=f'''# Constant-rebalanced pairs: large wealth-gap search

The search found historical pairs with the intended large compounding advantage. **CAR/INSM is the closest fixed-22-year result: 3.88× and 7.10× individually versus72.48× from daily50/50 rebalancing after modeled10bp trading costs.** GFI/INSM produces62.23× versus6.74× and7.10×. These are materially different from a small CAGR improvement over holding.

They are **historical research candidates, not five validated safe investments**. The five leading comparable examples all include Insmed, have72–92% maximum drawdowns, and were identified using the entire history. The chronological selection test does not reproduce an eightfold advantage in its later period. Independent bid/ask checks cover2018 onward, not the entire22 years.

## Comparable22-year results

2004-09-13 through2026-09-11. Start with equal dollar weights; rebalance every session to fixed50/50; no leverage, forecasts, optimized weights, stop-losses, switching, or portfolio capital transfers. Use adjusted total-return series and fractional units. Charge0.10% on **each dollar bought or sold**, including entry and liquidation. All multipliers use the same dates and matched entry/exit costs. An unchanged pair retains its initial shares with distributions reinvested in the same constituent.

{table(p)}

Companies: CAR=Avis Budget Group; INSM=Insmed; GFI=Gold Fields; HL=Hecla Mining; STAA=STAAR Surgical; CNO=CNO Financial. These primary candidates exclude the user's AI/Mag7 holdings. The CAR historical series includes the earlier Cendant business and distributions; it is not22 years of today's Avis operating business.

For a modeled$1,000 initial portfolio,72.48× means$72,475 terminal wealth **after22 years**, not a forecast or an annual return. CAR/INSM's92% drawdown is disqualifying for a low-risk mandate. This report does not equate a70× wealth multiple with70% per year.

Ranking bins used to describe the original example are: each stock ends at≤15×, rebalanced portfolio≥50×, and portfolio/better stock≥4×, sorted by the last measure. These summarize similarity to the example; they are not statistical significance thresholds or a claim of preregistration. Among the same22-year usable no-AI/non-meme cohort,41pairs meet these bins and **all41include INSM**. Two primary pairs exceed roughly8× the better stock; the other three are around7.5–7.7×.

## Other stock families, over longer histories

These are not mixed into the22-year ranking. The four January2000starts cover26.69years; HMY/INSM starts June2000. Drawdowns below are recomputed at the same10bp cost as returns. KGC=Kinross; NBIX=Neurocrine; IONS=Ionis; TREX=Trex; HMY=Harmony Gold; PEGA=Pegasystems.

{table(alt[['pair','start','end','years','stock_a_multiple','stock_b_multiple','rebalance_multiple','over_best','maxdd']].rename(columns={'pair':'Pair','years':'Years','stock_a_multiple':'A ×','stock_b_multiple':'B ×','rebalance_multiple':'Rebalanced ×','over_best':'Over better ×','maxdd':'Max DD %'}))}

KGC/NBIX is especially useful for separating the mechanism from INSM:6.03× and6.21× become50.63×. It still had a73.5% drawdown and needed a longer period than the original22-year example. The stored company snapshot shows positive profit and free cash flow for both KGC and NBIX, unlike INSM and IONS. That is a current financial screen, not a credit assessment or evidence of the next26years' return path. PEGA is enterprise software and is not assumed completely insulated from AI narratives merely because it is outside the explicit infrastructure exclusions.

## Exhaustiveness and exclusions

* Re-rank all1,817,360 company/identity-filtered longest-history pair results from the previous complete local panel. This is a ranking correction, not a claim that all of these were newly downloaded.
* Freshly calculate all579,426 pairs among1,077companies with complete common22-year histories, for both50/50 and55/45 and costs10/25bp:2,317,704 strategy configurations.
* Separately compute6,046,503 broad local symbol pairs across3,478histories with at least8continuous years through2026-09-11. This includes4,661,223pairs outside the earlier eligible long panel. These populations overlap; do not add their counts and call that many unique company pairs.
* The broad raw panel includes unresolved security types and problematic histories. Apply type/identity and basic history-quality proxies, and preserve rejected results.1,596,253additional pair rows meet those proxies plus positive cumulative rebalanced return. Shorter-window leaders include crypto/mining and distressed biotech with roughly90% drawdowns; they are not quiet-company substitutes for the primary shortlist.

The historical tradability proxy requires≤1%zero-volume sessions, a10th-percentile daily dollar-volume proxy≥$100,000, and no gross daily return above4 or below0.25. This is a coarse data/tradability screen, not proof of fills. Extremely large raw results from thin/inconsistent histories were retained in the raw output and excluded from recommendations. Company classification, history availability, and continuous-current-ending requirements cause survivorship and availability bias; this is exhaustive over the stated usable local cohorts, not every stock ever listed.

The requested ten-name-pair files are supplied separately: `top10_all_shape.csv` permits all sectors; `top10_non_ai_shape.csv` applies the AI/Mag7 exclusions; `top10_non_ai_non_meme_shape.csv` also removes explicitly identified GME/AMC/MSTR/COIN exposures. These are mechanical rankings, not ten investment endorsements. GME/INSM appears in the unrestricted lists at182.82×; it is omitted from the quiet-company shortlist because meme exposure conflicts with the user's mandate. A no-AI flag alone does not establish absence of hype.

## Cost and weight robustness

Ending rebalanced wealth multiples:

{table(costtable)}

The one/two-cent scenarios use half-spread costs of0.5/1cent divided by reconstructed nominal historical price, with a minimum10bp proportional cost. They are **hypothetical spread floors**, not measured2004–2017 quotes. They matter because a one-cent spread on a low-priced share can exceed10bp. At25bp, the five main results remain45.7–61.7×; at100bp they fall to20.0–27.5×. The mechanism remains material, but a specific70× result is not cost-invariant. No personal taxes are modeled, and the adjusted-total-return calculation assumes reinvestment of distribution value. Fixed55/45 is retained in the full grid and is not selected after the fact to maximize results.

## Independent arithmetic, event, and chronological checks

A separate holdings/cash implementation solves post-trade NAV with a numerical root finder and reproduces all{len(checks)}primary weight/cost calculations to1e-9relative tolerance. The fast grid does not simply average the two terminal returns.

The ten largest daily diversification-bonus observations contribute about19–24% of the primary pairs' total gross bonus. This is not a single-print artifact, but large events matter. The largest repeated Insmed event on2009-06-25 coincides with its unsuccessful IPLEX Phase II announcement; its2011one-for-ten reverse split is a different event, across which the local adjusted closing series moves5.00→5.35 rather than tenfold. See the [issuer's2009trial announcement](https://investor.insmed.com/2009-06-25-Insmed-Announces-Results-of-IPLEX-TM-Phase-II-Trial-in-Myotonic-Muscular-Dystrophy?asPDF=1) and [reverse-split announcement](https://investor.insmed.com/2011-03-02-One-for-10-Reverse-Stock-Split-of-Insmed-Common-Stock-Becomes-Effective). These corroborate specific events, not every historical quote.

Primary pairs beat their unchanged pairs in both2004–2014 and2014–2026 closing-price subperiods. Across overlapping monthly-entry10-year windows, all five beat their unchanged pairs in every tested window, while the fraction beating the better stock ranges84–96%. Shorter periods are weaker: INSM/STAA loses to its better stock over2018–2026, and wins only49% of five-year starts. Overlapping windows are not independent observations.

A stricter conditional chronological check ranks449,826eligible available-cohort pairs using2004-09-13–2014-09-12 returns only, requires positive rebalanced cumulative return, picks the top10by portfolio/better-stock ratio, and holds each pair fixed for2014-09-15–2026-09-11. It uses current availability/type/history-quality filters and therefore remains survivor-conditioned. It is not a clean point-in-time investable-universe test.

{table(chron[['pair','selection_over_best','subsequent_over_best','subsequent_over_hold','subsequent_maxdd']].rename(columns={'pair':'Pair','selection_over_best':'Earlier / better ×','subsequent_over_best':'Later / better ×','subsequent_over_hold':'Later / hold ×','subsequent_maxdd':'Later max DD %'}))}

Only5of10beat the better stock subsequently, and8of10beat their unchanged pair. None reproduces the earlier eightfold-type advantage in that later interval. **The historical existence of the effect passes; a reliable rule for selecting future spectacular winners has not passed.** The newly reranked primary shortlist uses2026data, so the file label `confirmation` must be read as a recent-period check, not an untouched holdout for this selection.

## Databento execution reconstruction

Original credential used; fallback remains unused. Newly requested XNAS.ITCH BBO1m and trading-status data cover12missing symbols from2018-05-01 through2026-09-11. Quote batch actual/quoted charge is${quote_charge:.6f}; status quote is${status['quote_usd']:.6f}. Reconciled prior actual-or-quoted usage plus these is approximately${estimated:.4f}; this is a research cost ledger, **not a queried account credit balance**.

Execution signals use two minutes before the regular or early close and fills use the next minute's venue bid/ask, plus0/5/10bp additional cost. Position sizing uses only the signal snapshot. The ledger accounts for sales before purchases, cash limits, displayed size, partial fills, trading halts/status gaps, and0/30-calendar-day dividend-cash lags. The starting model capital is$1,000with fractional units. XNAS.ITCH is venue data, not proof of a broker fill or a consolidated best quote. Snapshot execution does not reconstruct queue position, and fractional shares require broker support.

Attempted verification pool:57unique pairs (58ordered entries before removing a reversed duplicate). Actual reconstructed pairs:{execs[['a','b']].drop_duplicates().shape[0]}, with{len(execs)}period/cost/dividend scenarios. Pairs lacking necessary inputs are not counted as verified. The selected presentation below uses the more conservative30-day dividend lag plus10bp **in addition to the observed spread**. Status flags are part of the result, not discarded bad runs. Entry/exit depth flags also cover the unchanged baseline; an incomplete initial fill can leave it in cash, so flagged baseline comparisons are not accepted as validation of fully invested50/50.

{table(e[['pair','period','rebalance_multiple','hold_multiple','over_hold','maxdd','skipped_sessions','partial_fill_orders','status']].rename(columns={'pair':'Pair','rebalance_multiple':'Rebalanced ×','hold_multiple':'Hold ×','over_hold':'Ratio','maxdd':'Max DD %','skipped_sessions':'Skipped days','partial_fill_orders':'Partial fills'}))}

Period dates: `vendor_full` =2018-05-01–2026-09-11; `discovery` =2021-12-31–2025-12-31; `confirmation` =2025-12-31–2026-09-11. A status of `pass` means the stated execution checks passed, not that the strategy beat its benchmark.

All scenario statuses:

{table(stat)}

The independent nominal-close comparison covers2018onward for all primary names, with more than2,100sessions each and only a few discrepancies above0.1%. The22-year pre2018 action/price path remains sourced from the free/local adjusted history. Quote/close outliers, local mark fallbacks, skipped sessions, capacity flags, and individual scenario results are supplied in the files; no22-year executable bid/ask backtest is claimed.

## Corporate-action and risk limits

CAR includes Cendant's2006reorganization/spin-offs and reverse split. GFI includes its2013Sibanye distribution. The free source records adjustment factors across these events; the test assumes distribution value is reinvested into the tracked security, rather than retaining all distributed-company shares. These paths need complete corporate-action ledger reconciliation before the22-year figures can be treated as independently verified realizable returns. See [Avis issuer FAQ](https://avisbudgetgroup.gcs-web.com/ir-faqs), [Cendant SEC filing](https://www.sec.gov/Archives/edgar/data/723612/000095012306014372/y27169e10vq.htm), and [Gold Fields corporate transactions](https://www.goldfields.com/corporate-transactions.php). Insmed's business also changed with the [Transave combination in2010](https://investor.insmed.com/2010-12-02-Insmed-Incorporated-and-Transave-Inc-Announce-Business-Combination).

Best wealth ratio available under each drawdown cap in the same22-year, no-AI/non-meme, history-proxy cohort:

{table(risk[['drawdown_cap','pair','rebalance_multiple','over_best','maxdd','eligible_pairs']].rename(columns={'drawdown_cap':'DD cap %','pair':'Pair','rebalance_multiple':'Rebalanced ×','over_best':'Over better ×','maxdd':'Max DD %','eligible_pairs':'Eligible pairs'}))}

At a30% maximum-drawdown cap, the best observed ratio to the better stock is only1.33×. At50%, INSM/SCL reaches38.06× against a better constituent7.26×, a5.24×gap. These frontiers show the actual tradeoff; smaller-gap low-drawdown pairs are not relabeled as reproducing the original example.

## Decision

Keep investigating the constant-rebalancing mechanism: the data contain large historical effects, so rejecting it based on the earlier small-uplift rankings was unjustified. For the strict22-year pattern, prioritize GFI/INSM and HL/INSM over CAR/INSM; CAR is a historical benchmark match with extreme loss risk, not a safe deployment candidate. For a different stock family with stronger current financials, retain KGC/NBIX, explicitly with its longer history and still-large drawdown.

Do not fund five INSM combinations as if they were independent strategies, and do not treat these full-history rankings as a demonstrated forward selection edge. My stance is **promising historical mechanism and concrete candidates; not yet a validated low-risk live strategy**. The main unresolved issues are future pair selection, pre2018 executable costs/actions, and crash risk—not an inability to search obscure tickers. The arithmetic alone does not justify deploying capital today.

Reproducible outputs: `primary_shortlist.csv`, three top10files, `common22_all_pairs.parquet`, `broad_all_pairs.parquet`, `broad_audited_pairs.parquet`, `cost_stresses.csv`, `period_checks.csv`, `chronological_top10.csv`, `verification.json`, `execution_results.csv`, `primary_execution_summary.csv`, `long_history_alternatives.csv`, `risk_frontier.csv`, and `wealth_paths.png`. Research only; no orders or production strategy changes were made.
'''
# Improve automatically composed prose spacing around numbers without changing identifiers or links.
# Tables/files deliberately retain exact numeric precision and reproducible identifiers.
import re
parts=re.split(r'(`[^`]*`|https?://[^\s)]+|\d+e[+-]\d+)',text)
for i in range(0,len(parts),2):
 t=parts[i]
 t=re.sub(r'(?<=[A-Za-z])(?=\d|\$)', ' ',t)
 t=re.sub(r'(?<=\d)(?=[A-Za-z])(?!st\b|nd\b|rd\b|th\b)', ' ',t)
 t=re.sub(r'(?<=[%×])(?=[A-Za-z])', ' ',t)
 parts[i]=t
text=''.join(parts).replace('to1e-9','to 1e-9').replace('1e-9relative','1e-9 relative').replace('BBO 1 m','one-minute BBO')
text=re.sub(r'(?<=[A-Za-z])([,.:])(?=\d)',r'\1 ',text)
(P/'REPORT.md').write_text(text)
(P/'AUDIT_SUMMARY.json').write_text(json.dumps(dict(primary_pairs=5,independent_arithmetic_checks=len(checks),quote_status=spend['status'],fallback_used=False,execution_pairs=int(execs[['a','b']].drop_duplicates().shape[0]),execution_scenarios=len(execs),chronological_beats_best=int(chron.subsequent_over_best.gt(1).sum()),chronological_beats_hold=int(chron.subsequent_over_hold.gt(1).sum()),reconciled_actual_or_quote_usd=estimated),indent=2))
print('REPORT COMPLETE',flush=True)
