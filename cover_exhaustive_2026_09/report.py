from pathlib import Path
import json,hashlib
import pandas as pd,numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
P=Path(__file__).resolve().parent
T=pd.read_csv(P/'screened_two_lists.csv');S=pd.read_csv(P/'shortlist_summary.csv');D=pd.read_parquet(P/'shortlist_daily.parquet');A=pd.read_csv(P/'shortlist_annual.csv');F=pd.read_csv(P/'shortlist_features.csv');C=pd.read_csv(P/'shortlist_companies.csv');X=pd.read_csv(P/'shortlist_exposure.csv');FW=pd.read_csv(P/'familywise_shortlist.csv');counts=json.loads((P/'screened_counts.json').read_text());meta=json.loads((P/'search_metadata.json').read_text());coverage=pd.read_csv(P/'stock_coverage.csv');audit=pd.read_csv(P/'stock_price_audit.csv');gate=pd.read_csv(P/'tradability_stock_audit.csv');companies=C.set_index('symbol').company.to_dict()
def tab(df):return df.to_markdown(index=False)
def compact(g):
 rows=[]
 for _,r in g.iterrows():rows.append({'Rank':int(r['rank']),'Pair':r.pair,'2022–25 CAGR':f'{r.discovery_cagr_5bps:.2%}','Hold CAGR':f'{r.discovery_hold_cagr_5bps:.2%}','2022–25 drawdown':f'{r.discovery_maxdd_5bps:.2%}','2026 return':f'{r.confirmation_return_5bps:+.2%}','2026 hold':f'{r.confirmation_hold_return_5bps:+.2%}'})
 return pd.DataFrame(rows)
for label,g in T.groupby('list',sort=False):
 out=compact(g);out['Company A']=g.a.map(companies).to_numpy();out['Company B']=g.b.map(companies).to_numpy();out['2026 basic profitability and 10bps advantage check']=g.confirmation_basic_pass.to_numpy()
 filename='LIST_1_ALL_STOCKS.csv' if label=='all_stocks' else 'LIST_2_EX_AI_MAG7.csv';out.to_csv(P/filename,index=False)
 fig,axes=plt.subplots(5,2,figsize=(14,18),sharex=True)
 for ax,(_,r) in zip(axes.flat,g.iterrows()):
  for model,color,style,name in [('daily_50_50','black','-','Daily 50/50'),('hold_50_50','0.55','--','Hold initial 50/50')]:
   z=D[(D.pair==r.pair)&(D.period=='evaluation')&(D.strategy==model)&(D.cost_bps==5)]
   ax.plot(z.date,z.wealth/100000,label=name,color=color,ls=style,lw=1.3)
  ax.axvline(pd.Timestamp('2026-01-01'),color='.4',ls=':',lw=1);ax.set_title(f'{int(r["rank"])}. {r.pair}');ax.set_ylabel('Wealth / starting capital');ax.grid(alpha=.15);ax.legend(fontsize=8)
 fig.suptitle(('Full stock universe' if label=='all_stocks' else 'Excluding AI infrastructure / Mag 7 / named holdings')+'\nRanked through 2025; 2026 retained without replacement. Costs: 5 bps per traded dollar.',fontsize=14)
 fig.tight_layout(rect=[0,0,1,.965]);fig.savefig(P/f'curves_{label}.png',dpi=130);plt.close(fig)
raw=pd.read_csv(P/'two_lists.csv');raw=raw[raw['list']=='all_stocks'].head(10)
rawrows=[]
for _,r in raw.iterrows():
 a=coverage.set_index('symbol').loc[r.a];b=coverage.set_index('symbol').loc[r.b]
 rawrows.append({'Raw rank':r['rank'],'Pair':r.pair,'Apparent CAGR':f'{r.discovery_cagr_5bps:.1%}','Smaller leg median daily $ volume':f'{min(a.median_discovery_dollar_volume,b.median_discovery_dollar_volume):,.0f}','Larger zero-volume fraction':f'{max(a.zero_volume_fraction,b.zero_volume_fraction):.1%}'})
report='''# Exhaustive two-list rebalancing study

**Both requested lists are complete.** This study evaluated all 2,929,410 combinations of 2,421 stocks with sufficient individual adjusted history; 2,897,226 pairs supported at least 252 continuous paired daily returns. The findings support a historical rebalancing benefit in the selected paths, but do not establish a reliable profitable investment strategy.

The two final lists below are ranked through 2025, after a disclosed tradability/data-quality screen. They retain subsequent failures. They are the best by the stated conservative-growth criterion, not universally best investments or ten independent opportunities.

## What was searched, including exclusions

The file inventory scanned **108,592 data/archive files, 31.71 GB**, across user data folders, project folders and accessible cloud documents. It inspected tabular schemas and database schemas. All **21,004 binary market-data headers** were also read: they contain OPRA options or CME futures, not additional stock histories. The NSE bhavcopy archives contain futures/options. The stock sources included the daily Yahoo cache, a separate local SQLite database with 555 adjusted histories, and the broad mapped historical stock panel. Short intraday, split-only, duplicated and derived series were not mistaken for independent multi-year total-return histories.

There were **3,578 identifiable local stock/ADR symbols**, after ticker-alias consolidation and removal of funds and non-stock containers. Free Yahoo history was retrieved only for names already represented locally to repair adjustment/coverage gaps. No paid data or Databento purchase was made. The service rate-limited one pass; requests stopped, resumed more slowly after a successful probe, and all outstanding symbols received a recorded result. Across cached/retrieved requests, 2,531 succeeded and 1,047 failed; a few failed requests still had usable local-database fallback data.

Final coverage: **2,421 testable stocks**, **115 with fewer than 252 continuous daily returns**, and **1,042 without usable adjusted daily history**. Every one of the **6,399,253 possible combinations** of the identifiable universe is accounted for by stock eligibility and pair-overlap status. Within the testable-stock combinations, 32,184 pairs lacked sufficient continuous overlap; the other 2,897,226 were backtested. **2,390,391 pairs** had complete common discovery-period data. The complete results are retained in partitioned Parquet files, not just shortlisted winners.

This is exhaustive over identifiable, usable histories—not every US stock ever listed. Raw daily files also contain unmapped instrument IDs outside the saved symbol maps. Those cannot be assigned trustworthy company identities. Failed/delisted histories and intermittent historical mappings prevent a survivorship-free claim. Current thematic exclusions and the union of symbols collected over time are also not a historical point-in-time universe. Source availability is a limitation, not evidence that excluded stocks have no opportunity.

## Trading design and ranking

Each experiment begins with $100,000, divided 50/50. It restores 50/50 each close, long only, with fractional shares. No forecasts, leverage, stops, bands, pair rotation or optimized weights were added. Dividends and splits enter through adjusted daily prices. Comparators hold the initial 50/50 portfolio or either stock individually.

Costs apply to dollars actually bought and sold. Entry investment is `capital / (1 + cost)`; liquidation pays cost on proceeds. The numerical engine uses the exact self-financing 50/50 identity for intermediate sessions and has independent share-ledger checks. Tables use **5 bps (0.05%) per traded dollar**. Additional 0, 10, 25 and 50-bps cases are saved. Taxes, borrow (none used), closing-auction feasibility and dividend payment-date cash handling are not established by this proxy.

**Discovery is 2022–2025.** In each of two independent diagnostic halves (2022–2023 and 2024–2025), calculate annualized strategy log growth and annualized log wealth advantage over holding the same pair. The score is the minimum of these four quantities. Rank descending, then whole discovery CAGR advantage, then ticker symbols. This rewards positive absolute growth and recurring rebalancing benefit. It does not constrain drawdown; drawdowns are shown rather than hidden. Half-period comparisons reset the benchmark to 50/50 at each half's start; whole-period runs do not reset or switch pairs.

**Confirmation is January 2–September 11, 2026**, starting from December 31, 2025 prices. Its returns were not used to replace or reorder the selected candidates. These are historical confirmation observations, not a pristine untouched prospective trial: parts of the history were already used in previous research and the eligibility amendment below was exploratory.

The second list excludes the named holdings, Mag 7, semiconductors/equipment, AI server/network/storage businesses, data-center infrastructure, AI compute businesses and a documented conservative AI-related thematic overlay. It does not ban all technology use, cryptocurrency, biotech, coal, or every speculative theme. There are no same-sector or repeated-stock prohibitions. A factual taxonomy review added omitted infrastructure suppliers such as ARM, IBM and Oracle to the final exclusion file, applying the correction to the entire screened universe; neither delivered top-ten list changed. Both taxonomy versions and the timing disclosure are saved in EXCLUSION_REVIEW.md.

## Why the raw winners were not presented as credible candidates

The initial, unfiltered all-pair ranking produced the following. Both raw lists were identical:

'''+tab(pd.DataFrame(rawrows))+'''

These apparent returns cannot support a $100k daily-rebalanced implementation at 5–10 bps. LMPX traded around $200 of median daily dollar volume in the cached discovery data, versus a proposed $50,000 stock allocation; 36% of its sessions reported zero volume. SCPS and TCCO were still less liquid. Some histories also have material adjustment discrepancies. These results remain saved rather than being discarded from the research record.

**Disclosed amendment:** after observing those invalid leaders, before inspecting revised rankings, apply to every stock: in each discovery half, median daily dollar volume >=$5m, lower-decile daily dollar volume >=$1m, strictly positive volume every session, and no unresolved adjusted-return versus close-plus-dividend discrepancy >0.5 percentage points during discovery. These thresholds relate to the initial $50k leg being <=1% of median turnover and <=5% of lower-decile turnover. They are feasibility conventions, not verified fills. No price, correlation, sector, growth or drawdown cutoff was added. This amendment is explicit and means the entire specification was **not** fixed before any results.

That leaves **1,515 stocks / 1,146,855 pairs** for the full screened ranking and **1,377 stocks / 947,376 pairs** after the AI/Mag 7 exclusions. Every screened pair was already part of the exhaustive run.

## List 1 — full stock universe

All figures below include 5-bps costs. CAGR columns cover four discovery years. 2026 columns are partial-year total returns, not annualized.

'''+tab(compact(T[T['list']=='all_stocks']))+'''

## List 2 — excluding AI / Mag 7 / named holdings

'''+tab(compact(T[T['list']=='without_ai_mag7']))+'''

The two lists overlap by design: their universes overlap. They contain **11 unique pairs and 15 unique stocks**. No effort was made to force different names merely to make the lists look more diverse. SMCI/VKTX is excluded from list 2 because Supermicro supplies [AI infrastructure](https://www.supermicro.com/en/solutions/ai-deep-learning); MSTR/TGTX enters at the next unchanged rank.

## What the subsequent results actually show

Every full-list pair outperformed holding the same pair in 2026, including at 10 bps. **Eight nevertheless lost money.** The corresponding non-AI list also has positive relative performance at 5 bps throughout, but MSTR/TGTX loses its very small advantage at 10 bps. Rebalancing can reduce a loss without producing an attractive absolute investment return.

Only **SMCI/VKTX** and **GFI/MSTR** in list 1 passed the basic 2026 check of positive absolute return and continued advantage at both 5 and 10 bps. Only **GFI/MSTR** passed in list 2. This is a simple screen, not validation or a deployment recommendation. It does not elevate those pairs above the original ranking or remove the failures.

For context, about 49.7% of all screened full-universe pairs with complete confirmation passed that same basic check, compared with 2/10 of the selected leaders; about 47.9% passed in the restricted universe, compared with 1/10. These are descriptive, dependent observations—not independent binomial trials. The highest historical conservative-growth scores did not identify a reliably profitable subsequent top-ten list.

## Costs, risk and concentration

'''
z=S[(S.period=='confirmation')&(S.strategy.isin(['daily_50_50','hold_50_50']))]
costrows=[]
for pair in T.pair.drop_duplicates():
 d=z[z.pair==pair].set_index(['strategy','cost_bps']);costrows.append({'Pair':pair,'2026 at 5 bps':f"{d.loc[('daily_50_50',5),'total_return']:+.2%}",'2026 at 10 bps':f"{d.loc[('daily_50_50',10),'total_return']:+.2%}",'2026 at 50 bps':f"{d.loc[('daily_50_50',50),'total_return']:+.2%}",'Hold at 50 bps':f"{d.loc[('hold_50_50',50),'total_return']:+.2%}"})
report+=tab(pd.DataFrame(costrows))+'''

Discovery drawdowns ranged from **54% to 69% in list 1**, and reached **75% in list 2**. These are substantial capital losses along the path despite high terminal historical growth. Turnover was roughly 5.6–6.9 times capital per year, counting buys plus sells and initial/final trades.

VKTX appears in **six of ten pairs in list 1** and **five of ten in list 2**. If all ten were equally funded, that would imply 30% and 25% initial total exposure to one stock, respectively. This is a concentration diagnostic, not a proposed combined portfolio. Correlations with an equal-weight proxy of the user's eleven named holdings ranged approximately 0.26–0.51 in discovery and 0.17–0.55 in confirmation; actual user portfolio weights are unknown.

Excluding AI is not equivalent to avoiding hype or event risk. [Viking](https://vikingtherapeutics.com/pipeline/vk2735/) and [Rhythm](https://rhythmtx.com/science/) have obesity-related drug programs; [Strategy](https://www.strategy.com/investor-relations?pr=1151) is a Bitcoin treasury company; [Archer](https://archer.com/) develops electric air taxis; [Ramaco](https://ramacoresources.com/) combines metallurgical coal with an exploration-stage rare-earth project. These facts materially limit the fit with the original desire for unremarkable, independent businesses. The rankings were not altered to conceal that mismatch.

## Volatility harvesting versus stock appreciation

For gross returns `gA, gB`, daily 50/50 log growth is `0.5*log(gA) + 0.5*log(gB) + bonus`, with `bonus = log((gA+gB)/2) - 0.5*log(gA*gB)`. The gross bonus is nonnegative, but it is measured against the weighted geometric growth of the stocks. It is not a guarantee of beating buy-and-hold, either stock, or cash after costs.

'''
r=F[F.period=='discovery'][['pair','weighted_stock_log_growth','diversification_log_bonus','top5_days_share_of_diversification_bonus']].copy()
for col in r.columns[1:]:r[col]=r[col].map(lambda x:f'{x:.2%}')
r.columns=['Pair','Stock contribution: annual log growth','Bonus: annual log growth','Bonus from largest five days'];report+=tab(r)+'''

Much of the extraordinary discovery CAGR came from underlying stock appreciation. The five largest bonus days account for approximately 8%–40% of the diversification bonus in these pairs. This is event concentration, not a smooth assured yield. Four years cannot replicate or validate the cited 22-year Cover example, and none of these returns is projected forward.

## Multiple testing and accounting checks

A common-date, 20-session Gaussian block-multiplier maximum check with 499 draws considered all **2,390,391 discovery-rankable pairs**. Studentized maximum statistics were also calculated for the screened family of 1,146,855 pairs to avoid raw volatility scale dominating the comparison. None of the shortlist passes a 5% family-wise threshold under this approximation. This check is conservative and dependent on the block model; its high adjusted p-values are not proof that the rebalancing mechanism is absent. It means the historical search has not established statistical significance after selection. Full multiplier outputs and code are retained. The raw unstudentized maximum is particularly distorted by illiquid histories and is not presented as a useful effect-size threshold.

The engine passed **90 independent random-path share-ledger reconciliations**, a Cover doubling/halving identity, an equal-return/no-rebalance case, and a missing-price gap test. Shortlisted paths passed **198 additional CAGR reconciliations** covering primary and buy-and-hold benchmarks across full, discovery and confirmation periods. The supplementary continuous 2022–2026 evaluation and 25/50-bps stress tests also use the independent cash/share ledger. Complete curves, annual returns, constituent returns, turnover and cost dollars are saved.

Selected stocks have no >0.5-percentage-point adjustment discrepancy in the discovery gate. This is an internal consistency check, not independent vendor verification. Comparison against the locally stored, intermittent Databento-derived split-only panel still flags material return differences—for example, SMCI has 30 differences above 0.10 log-return units among 846 comparable observations. Session-close conventions, gaps and corporate-action handling need reconciliation; the source of each discrepancy has not been established. Current stock listings were checked against the [official Nasdaq symbol directory](https://nasdaqtrader.com/Trader.aspx?id=symbollookup), and all 15 shortlisted stocks were present. That present-day listing check was not used to reorder historical candidates.

The largest modeled rebalance-or-exit trade reached roughly 0.3%–2.9% of reported daily dollar turnover in the discovery experiments. Daily turnover does not establish closing-auction depth. Some stocks also traded below $5 during discovery. A 5-bps fee scenario therefore remains an assumption, even after the liquidity screen. Exact 50/50 orders based on an unknown closing print require a causal execution specification before a live interpretation is justified.

## Deliverables and next step

The requested deliverables are `LIST_1_ALL_STOCKS.csv` and `LIST_2_EX_AI_MAG7.csv`, with the full evidence below. The highest-scoring discovery pairs have now been found across the available usable data. They have not been replaced by prettier 2026 winners. There is evidence of relative rebalancing benefit, severe risk and substantial thematic concentration; there is not yet an established profitable independent strategy.

If advancing to independent measurement verification, the 2026-profitable checks make SMCI/VKTX relevant to the unrestricted list and GFI/MSTR relevant to both lists. The former is an AI-related pair; the latter is exposed to Bitcoin and gold. Neither should be described as satisfying a broad no-hype mandate. The Databento specification preserves every candidate and the failed results while prioritizing those measurement checks. No paid acquisition or live trade occurred.

- `PROTOCOL.md`: original frozen score and design.
- `TRADABILITY_AMENDMENT.md`: timing, rationale and exact added eligibility rules.
- `file_inventory.csv`, `binary_metadata.csv`, `DATA_AUDIT.json`: disk/source audit.
- `local_mapped_stock_universe.csv`, `stock_coverage.csv`, `source_manifest.csv`: stock provenance, failed requests and exclusions.
- `pair_eligibility_counts.csv`, `search_metadata.json`: reconciliation of every combination.
- `all_pairs/part_*.parquet`: every tested or overlap-ineligible combination among testable stocks.
- `all_ranked_pairs.parquet`, `all_screened_ranked_pairs.parquet`: complete rankings, not just winners.
- `top10_all_stocks.csv`, `top10_without_ai_mag7.csv`: preserved raw, non-executable leaders.
- `tradability_stock_audit.csv`, `ai_mag7_exclusions_v2.csv`: final screen decisions; the original taxonomy is also retained.
- `shortlist_summary.csv`, `shortlist_daily.parquet`, `shortlist_annual.csv`: full benchmarks and cost cases.
- `shortlist_features.csv`, `shortlist_exposure.csv`, `shortlist_concentration.csv`: growth attribution and dependence.
- `stock_price_audit.csv`, `price_events.csv`, `shortlist_verification.csv`, `engine_verification.json`: checks and unresolved flags.
- `familywise_shortlist.csv`, `familywise_maxima.csv`, `familywise_metadata.json`: multiple-testing diagnostics.
- `price_matrix.parquet`, `prices.npy`, `calendar.json`, `matrix_symbols.json`: frozen numerical input matrix, hashed in search metadata.
- `curves_all_stocks.png`, `curves_without_ai_mag7.png`: continuous 2022–2026 comparisons; dotted line marks confirmation start.
'''
(P/'REPORT.md').write_text(report)
(P/'FINAL_TABLES.md').write_text('Full universe\n\n'+tab(compact(T[T['list']=='all_stocks']))+'\n\nExcluding AI / Mag 7 / named holdings\n\n'+tab(compact(T[T['list']=='without_ai_mag7']))+'\n')
print('Report and two lists written',flush=True)
