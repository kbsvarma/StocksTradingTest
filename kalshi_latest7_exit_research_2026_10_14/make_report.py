from pathlib import Path
import json,gzip
P=Path(__file__).resolve().parent
d=json.loads((P/'selected_results.json').read_text());o=json.loads((P/'original_only_results.json').read_text());diag=json.loads((P/'seven_diagnoses.json').read_text());rejections=json.loads((P/'candidate_rejections.json').read_text());proof=json.loads((P/'selected_print_probe.json').read_text());ledgers=json.load(gzip.open(P/'original_only_ledgers.json.gz','rt'))
lines=[]
def w(t=''):lines.append(t)
def row(label,m):w(f"{label:29} {m['loss_savings']:10.4f} {m['losses_reduced']:4} {m['winners_cut']:5} {m['winner_cost']:10.4f} {m['net']:11.4f} {m['delta']:11.4f}")
def header():w('Design                          Saved $  Lred  Wcut  Wdamage $       Net $    vs hold $')
w('LATEST SEVEN LOSSES: Z, TIMING AND FIXED EXIT PRICES')
w('14 October 2026 research cutoff, markets closed through 22:00 ET. Research completed 15 October UTC. Live bot, paused lanes and all existing observers unchanged.')
w()
w('RESULT AND DECISION')
w('The previous time windows disabled BTC/WTI stops before the losses crossed their negative-z thresholds. NATGAS had no fitted exit. USD/CAD stayed positive in the proxy-z series but lost when its official expiration equaled the strike and the market settled YES.')
w('A better historical trade-off exists when we attempt early exits at fixed minimum resale prices, rather than accepting any low bid after a reversal. Original WTI plus original NATGAS recovers $9.3673 from two recent losses, cuts two winners at $0.5320 total cost, and raises the later 111-trade net from $1.4259 to $10.2612. Adding original BTC rescues one more loss and raises later net to $14.4165, but cuts seven additional earlier winners.')
w('These are fitted historical QUOTE SCENARIOS. They are not filled orders, a new live performance record, or an out-of-sample edge. The latest seven are explicitly used for research in this follow-up. All 461 entries, 438 winners and 23 losses remain in the accounting, including paused assets and both lanes.')
w('Priority for prospective paper verification: original WTI plus original NATGAS. BTC is a secondary candidate with more winner cuts and parameter sensitivity. No collector or paper service is started by this research, and no live changes are made.')
w()
w('WHY THE SEVEN WERE MISSED')
why={
'KXWTI15M-26OCT121430-30':'z first fell below -1 with 116 seconds left; the previous rule required at least 120. Removing that cutoff alone does not establish recovery: the minute-low bid at the crossing is zero.',
'KXNATGAS15M-26OCT122000-00':'No fitted stop was assigned because there were zero earlier loss examples. z stayed above 2 until 18 seconds remained, and below 1.5 only at 11 seconds; waiting for a collapse was late.',
'KXWTI15M-26OCT131915-15':'z first fell below -1 at 40 seconds remaining, outside the old window. A long period of low cushion earlier in the trade was missed by that deep stop.',
'KXBTC15M-26OCT140615-15':'z first fell below -0.5 at 54 seconds remaining, outside the old window. z had already weakened below 1.5 at 318 seconds and below 0.5 at 263 seconds.',
'KXBTC15M-26OCT140830-30':'z first fell below -0.5 at 19 seconds remaining. Earlier weakness near z2 occurred while an 85-cent minute-low bid was still recorded. Proxy reference also differs from the official strike by $33.10.',
'KXUSDCAD15M-26OCT142000-30':'No fitted exit, only 59.06 seconds held, and the reconstructed minimum z was +0.1891. Official expiration and strike both 1.42135; held NO lost. A neutral zero-z stop would not fire.',
'KXWTI15M-26OCT142130-30':'z first fell below -1 at 82 seconds remaining, outside the old window. An earlier warning occurs, but the fixed high resale price is no longer supported by the minute-low quote.'}
for r in diag:
 w(f"{r['ticker']}: original loss ${-r['hold_net']:.4f}, entry z {r['entry_z']:.4f}, held {r['close']-r['entered']:.1f}s. {why[r['ticker']]}")
w('Total seven-loss outlay: $31.8133. Actual hold P&L of all 111 surrounding trades is +$1.4259, because the 104 winners earned $33.2392. Do not compare seven-loss-only proceeds to full-period net without including those winners.')
w()
w('EXACT NEW RULES — ORIGINAL LANE ONLY')
w('All use one-second checks and one-second assumed execution latency. Absolute signed z, frozen original entry reference and volatility. Negative z means crossing the strategy reference against the held side. These are exit additions to the preceding research map, not changes to actual live entries.')
w('WTI: during the final five minutes, require z < 1.75 continuously for 60 seconds, then attempt a sale with a 90-cent minimum price.')
w('NATGAS: during the final two minutes, if z < 5, attempt a sale only at 98 cents or better. This is an early high-price cashout; it does not wait for a negative-z loss signal. A 90-second start gives the same sample result. The 120-second version avoids choosing the finely optimized 65-second boundary.')
w('BTC alternative: after at least 60 seconds of observed holding history, require current z < 2 AND a fall of at least 1 z point over the preceding 60 seconds; attempt a sale only at 80 cents or better. No extra confirmation delay.')
w('An attempted sale below its fixed limit is unfilled in the model. The first trigger gets one modeled attempt; it is not repeatedly retried until a historical high appears. The previously studied deeper fallback remains available if no earlier modeled sale occurred. Earliest successful modeled exit closes the position once; no double counting.')
w('Premium positions remain at holding in the original-only sensitivity. We also tested applying exits to both lanes; that cut additional premium winners with no loss recovery because this sample has 63 premium wins and zero premium losses. This lane restriction is another explicitly hindsight-informed choice, not independent validation.')
w()
w('LATEST 111 TRADES, INCLUDING ALL SEVEN LOSSES AND 104 WINNERS')
header();row('Prior research exit map',d['prior_map']['periods']['later'])
for label,k in [('Add original WTI','WTI_only_addition'),('Add original NATGAS','NATGAS_harvest_addition'),('Add original WTI + NATGAS','WTI_NATGAS_additions'),('Add original BTC + WTI + NAT','BTC_WTI_NATGAS_additions')]:row(label,o[k]['periods']['later'])
w('The main WTI+NATGAS combination recovers 29.4% of the seven original loss dollars. With BTC it recovers 42.5%. Four of the seven remain unrescued by the broader combination; no claim that every loss can be avoided.')
w()
w('ALL 461 HISTORICAL TRADES — SAME ENTRIES, SIZES AND ENTRY FEES')
header()
row('Prior research exit map',d['prior_map']['periods']['full'])
for label,k in [('Add original WTI','WTI_only_addition'),('Add original NATGAS','NATGAS_harvest_addition'),('Add original WTI + NATGAS','WTI_NATGAS_additions'),('Add original BTC + WTI + NAT','BTC_WTI_NATGAS_additions')]:row(label,o[k]['periods']['full'])
w('Actual holding net is +$28.5536. The prior +$44.7112 is itself a hypothetical exit comparison, not actual bot P&L. All full-history net figures retain the prior research-map exits on other assets, including the earlier Copper exit scenarios; no paused lane has been restarted.')
w('WTI+NATGAS adds $8.8353 net to that prior exit map; BTC adds a further $4.9194. The latter includes $0.7640 incremental earlier-period improvement and $4.1553 from the recent BTC loss, after the seven earlier winner cuts.')
w('Applying the WTI+NATGAS additions to both lanes gives full net $53.2327, four winners cut costing $0.8458. Applying all three to both lanes gives $57.5178, twelve cuts costing $2.7440. Original-only versions avoid the three extra premium cuts.')
w()
w('RECOVERED LOSSES AND CUT WINNERS — ORIGINAL-ONLY THREE-ASSET VERSION')
w('Ticker                            Result Qty Left(s) Bid(c)   Hold net   Exit net   Change')
for x in ledgers['BTC_WTI_NATGAS_additions']:
 if x['close']<1791777600 or not x['exit']:continue
 w(f"{x['ticker']:33} {'win' if x['won'] else 'loss':6} {x['quantity']:3g} {x['close']-x['trigger']:7} {x['price']*100:6.1f} {x['hold_net']:10.4f} {x['net']:10.4f} {x['delta']:9.4f}")
w('The NATGAS loss becomes +$0.2173 in this model. The WTI loss shrinks from -$4.5457 to -$0.0215. The BTC loss shrinks from -$4.3422 to -$0.1869. Combined three losses improve from -$13.5137 to +$0.0089. Recoveries include exit costs already.')
w('The two recent winners affected lose $0.5320 relative to settlement: WTI +$0.3695 becomes -$0.0056; NATGAS +$0.3742 becomes +$0.2173. The extra BTC rule cuts seven earlier original-lane winners costing $1.2639. Historical quantities are preserved; several older BTC positions were only one or two contracts, not five.')
w()
w('WHY THE REMAINING FOUR ARE NOT COUNTED AS SAVED')
for r in rejections:
 if r['exit']:continue
 w(f"{r['ticker']}: {r['reason']}; signal at {r.get('remaining')} seconds remaining, minute-low quote {r.get('quote_low_at_attempt')}, minimum resale price {r['sell_limit']}.")
w('The two remaining WTI sales fail the 90-cent minimum at 69c and 75c; the remaining BTC sale fails its 80-cent minimum at 39c. Lower prices would recover some stake, but the broader winner tests show more costly false exits. Lowering the WTI floor to 85c makes its standalone rule lose $0.6038 versus holding across WTI; lowering BTC to75c creates 13 full-history winner cuts in the all-lane sensitivity. These failures are retained.')
w('USD/CAD is UNKNOWN at its actionable warning, not proven unsellable. z <0.75 fires with43 seconds left. Eighteen saved order books cover57.89 to47.42 seconds left, ending4.42 seconds before that warning. Subsequent public prints over10 seconds span69c–86c NO, but they are not executable bid depth. We do not substitute those trades as fills. A shared z0.75 last-minute rule also flags8 EUR/USD winners; their minute-low modeled exit damage is $25.7259. Fitting USD/CAD alone to its sole loss provides no winner calibration.')
w()
w('EXECUTION EVIDENCE AND ITS LIMITS')
w('The old continuous BTC/ETH feed/book archive ends October4, before these seven losses. Execution-observer receipts retain entry-side depth around qualified entries, not a continuous held-side liquidation book. Resting-paper receipts provide only the partial USD/CAD window described above. There is no paired, full-quantity bid-depth proof for the three proposed winning exits.')
w('Independent public transaction checks immediately after the modeled exits:')
for x in proof:
 w(f"{x['ticker']}: {x['prints']} prints / {x['contracts_printed']:.2f} contracts in the following10 seconds, held-side prices {100*x['min_print']:.1f}–{100*x['max_print']:.1f}c; minute-low model uses {100*x['price_model']:.1f}c.")
w('These transaction prices are consistent with the quote scenarios, but do not prove our order would have filled. NATGAS has only5.36 contracts printed in that10-second interval, close to the five-contract target; execution verification matters particularly there. No fabricated full-depth fill is reported.')
w('Full-history prices use the minimum held-side bid in the minute containing trigger+latency. This can assign zero to a late attempted exit even when earlier prices in the same minute were high; it is an ex-post stress scenario, not a tick replay or guaranteed lower bound. Conversely, a positive low does not prove enough quantity for our order. Prices are never used to generate a z signal; fixed resale limits only determine whether the modeled sale is accepted.')
w()
w('ROBUSTNESS AND OVERFITTING')
w('Extending latency from1 to3 seconds preserves the new three recent modeled recoveries, but changes an older fallback exit. Raising exit friction to2c per contract retains a positive historical improvement. These tests remain at minute-quote resolution, so they cannot certify subsecond execution.')
w('WTI is parameter-sensitive: standalone all-lane incremental net is +$4.1491 at60-second confirmation, but -$1.6284 at45 seconds and -$0.9566 at75 seconds. z1.5 does not rescue the recent loss; z2 cuts nine winners. This is a material reason not to deploy directly.')
w('NATGAS: z5 at90 or120 seconds gives the same modeled recovery; z6 andz8 still recover the loss but cut more winners. Starting only in the last60 seconds loses the modeled fill, strongly affected by the minute-bar boundary. A99c sell minimum does not fill;97c cuts more eventual winners. Only one historical NATGAS loss exists, so its apparently strong net is not independently validated.')
w('BTC: z1.75 or a30-second lookback misses the recent rescue; an85c minimum still rescues the recent loss but worsens earlier net by$1.5183 in the standalone all-lane sensitivity. A75c floor cuts more winners. The chosen rule is exploratory; nearby favorable cases do not remove this sensitivity.')
w('All latest-period numbers are now in-sample. Selection, parameter expansion, exit-price floors and lane restrictions were explored after seeing these losses. Earlier-period comparisons expose collateral damage but cannot turn this into a clean holdout. No estimated future win rate or probability of profit is asserted.')
w()
w('METHOD AND AUDIT')
w('Reused3,723 earlier configurations, then screened9,313 targeted z/time/deterioration rules with fixed-sale-limit sensitivities and2,592 late-cashout configurations. Search failures and intermediate frontiers are saved. The finer65-second NATGAS optimum is retained as a diagnostic, while the displayed candidate uses a round120-second window.')
w('All actual historical entries and fees remain fixed, including original and premium lanes and paused Copper/USD-CAD. This is an exit counterfactual, not a replay of current entry gates. The recent two BTC entries preceded the current original-BTC z3 change, so their rescue is not automatically a gain available to current entries.')
w('Signed z uses original reference/sigma and changing time to expiry. Crypto uses CF price changes anchored to recorded Coinbase entry spot; financials use Pyth historical prices. Historical source availability is assumed1 second after timestamp, not proven first arrival. Official metadata confirms BTC averaging/reference differences and USD/CAD tie; no official-strike exit model was substituted silently.')
w('Costs: actual entry fees; modeled0.07*q*p*(1-p) exit fee rounded up at1e-6, cash rounded down at1e-4, plus1c per exited contract friction. All saved/net figures include these costs. Sale proceeds can exceed original outlay on a future loser, as with NATGAS; that creates a modeled small profit rather than merely avoiding its loss.')
w('2,000 independent scalar/grid checks and137 Decimal exit-cash checks passed. Every cohort remains461 trades and23 losses. Original-only reconciliation retains all63 premium trades at holding. Production source hashes, ARM/HALT flags and five service states/PIDs match the previous preservation receipt; no financial order calls or production changes.')
w('Artifacts: PLAN.txt; seven_diagnoses.json; existing_grid_frontier.json; configs.json/arrays.npz/frontier.json; limit_frontier.json; expiry_configs.json/expiry_arrays.npz/expiry_frontier.json; selected_results.json/selected_ledgers.json.gz; original_only_results.json/original_only_ledgers.json.gz; candidate_rejections.json; public_summary.json and public receipts; selected_print_probe.json; resting_book_coverage.json; audit.json. Scripts reproduce calculations from the frozen datasets in kalshi_market_z_stops_2026_10_14.')
(P/'REPORT.txt').write_text('\n'.join(lines)+'\n');print(P/'REPORT.txt')
