"""Render reproducible plain-text research report and exhaustive loss audit."""
from portfolio import *
def load(n):return json.loads((P/n).read_text())
def money(x):return f'{x:+.4f}'
def main():
 actual=load('actual_entries.json');minute=load('actual_minute_results.json');fast=load('actual_fast_results.json');depth=load('depth_grid_results.json');port=load('portfolio_results.json');diag=load('diagnostics.json');fs=load('fast_entry_screen.json');dets=json.load(gzip.open(P/'actual_fast_details.json.gz'));ml=json.load(gzip.open(P/'actual_minute_ledgers.json.gz'));sel=load('entry_selections.json');lines=[]
 def w(s=''):lines.append(str(s))
 def table(head,rows):
  w(' | '.join(head));w(' | '.join('-'*len(h) for h in head))
  for row in rows:w(' | '.join(str(x) for x in row))
 w('ALL-LANE ENTRY / EXIT STUDY — October14,2026 frozen capture')
 w('Research completed October15 UTC. No live or paper bot strategy was changed.')
 w('Scope: RESEARCH_SCOPE.txt; reproducible scripts and full grids in this directory.')
 w()
 w('DECISION')
 w('Do not deploy a blanket z-stop, lower entry thresholds across the bot, or resume Copper/USD-CAD from this study. Earlier exits can reduce individual losses; the whole-history evidence does not establish a reliable net improvement after sacrificing winners. A wide two-point z-drop remains an isolated prospective-paper candidate, not a validated production improvement. No new paper service was started.')
 w('The best small-sample fast-book result does not generalize automatically: only20 actual entries/2losses have continuous subminute depth and first-arrival source receipts. All459 actual entries and all22 losses were included in the broader minute/fast-source studies. Exact all-trade 1s/10s/30s fill P&L cannot be recovered from minute candles.')
 w()
 w('WHAT WAS TESTED')
 w('Nine assets, original and premium lanes, including paused Copper and USD/CAD. Actual-ledger cutoff is ledger_summary.json:459settled trades,437wins,22losses; actual mixed-size net+$33.1242. September12 onward actual history; older source-based replays are separate, not mislabeled actual trading.')
 w('320 exit rules: entry-z drops .25/.5/.75/1/1.5/2; absolute z0/.5/1/1.5; entry-gate drops0/.5/1; fractions .25/.5/.75; price declines5/10/15/20c. Holding delays0/30/60/120s; elapsed breach confirmations0/10/30/60s; polls1/10/30/60s.320minute rules+1,280fast-source rules+1,280recorded-depth rules retained, including failures.')
 w('259 entry settings per asset: current rules; constants1/1.25/1.5/1.75/1.8/2/2.25/2.5/2.75/3/3.5/4; original-only,premium-only,paired z/z+1; windows1–3,3–5,5–10,10–13,1–10,3–10,1–13m; six shifts to current adaptive profiles.9×259×320=745,920 entry/exit/asset minute-screen combinations, plus hold controls. These reconstruct opportunities, not merely filtering filled trades.')
 w(f'Fast joint sensitivity: {len(fs["results"])} asset/entry/interval combinations ×{len(fs["exit_rules"])} representative exits, with explicit complete-source denominators. Full noncrypto entry coverage; crypto fast histories cover selected actual-entry hours, so new crypto opportunity subsets are selection-biased and cannot establish whole-universe frequency or edge.')
 w(f'{len(port)} event-driven portfolio scenarios, four periods, repeated with2c/5c friction. Shared25contracts/$25 exposure/$20 daily including reserved open risk;5contracts per entry; asset-exposure lock; one filled owner per market; premium25/$25/$25withinshared; $100 hypothetical cash split$50per route. Periods reset paper cash; full-period run is separately available. Actual balances and historical capital changes are not being reproduced.')
 w()
 w('ACTUAL TRADE BASELINE AND GENTLER EXIT')
 w('Rule: z drops2 below entry z, minimumhold120s, breach lasts60s, observed on completed-minute source values. On minute checks the exit normally requires two breached observations. Subsequent-minute bid proxy; modeled exitfee and1c/contract friction. These are not recorded-depth fills.')
 chosen=next(r for r in minute if r['config']['id']=='drop_2_g120_p60')
 table(['Asset','Trades','W/L','Hold net','Exit net','Losses reduced','Saved','Winners cut','Profit sacrificed'],[[a,m['trades'],f"{m['wins']}/{m['losses']}",money(m['hold']),money(m['net']),m['losses_reduced'],money(m['recovered']),m['winners_cut'],money(m['winner_cost'])] for a,m in chosen['assets'].items()])
 w('Total: +33.1242 +11.4796 loss recovery −9.5220 winner sacrifice = +35.0818; incremental +1.9576. Ten losses reduced, zero completely avoided, four winners turned into losses. Recovery is not the final P&L; the difference is not all fees.')
 m=chosen['all'];w(f"Exit fees${m['exit_fees']:.4f}; additional friction${m['friction']:.4f}.95% exploratory day-block interval for incremental net: {diag['drop_2_g120_p60']['uncertainty']['interval95']}. It includes no improvement. Existing history was previously explored; not an untouched holdout or multiple-testing-corrected result.")
 n=diag['drop_2_g120_p60']['normalized5'];w(f"Synthetic all5contract normalization: hold{money(n['hold'])}, exit{money(n['net'])}, improvement{money(n['delta'])}. Actual entryfee perunit scaled linearly; not new historical trades or exact exchange rerounding. The actual mixed-size ledger is the primary comparison.")
 table(['Exit rule','Losses reduced/22','Winners cut/437','Loss recovery','Winner sacrifice','Net'],[[r['config']['id'],r['all']['losses_reduced'],r['all']['winners_cut'],money(r['all']['recovered']),money(r['all']['winner_cost']),money(r['all']['net'])] for r in minute if r['config']['id'] in ['drop_1_g0_p0','drop_2_g0_p0','drop_2_g120_p60','price_0.1_g0_p0','price_0.2_g0_p0']])
 w('Selecting an exit on preOct8 actual data picks20c price deterioration; later-period delta is−15.0894, totaldelta−13.2873. The gentle2point/120s/60s rule has earlydelta−3.1705, laterdelta+5.1281. Neither demonstrates a stable full-period improvement. Grace120s cannot rescue an entry held less than120s, including the59s USD/CAD loss.')
 table(['Lane','Trades','Losses','Hold','Exit','Reduced','Winners cut'],[[k,m['trades'],m['losses'],money(m['hold']),money(m['net']),m['losses_reduced'],m['winners_cut']] for k,m in chosen['lanes'].items()])
 table(['Entry time remaining','Trades','Losses','Hold','Exit','Reduced','Winners cut'],[[k,m['trades'],m['losses'],money(m['hold']),money(m['net']),m['losses_reduced'],m['winners_cut']] for k,m in diag['drop_2_g120_p60']['bytime'].items()])
 w()
 w('FAST CHECKS: ALL459 ACTUAL ENTRIES, ALL22 LOSSES')
 w('Complete historical second-source coverage for all459 holding periods.262authenticatedGET-only CFhour receipts; crypto CF price changes anchored to actual entry Coinbase spot. Noncrypto Pyth source timestamps. Frozen entry volatility, assumed1s source delivery; these are experimental fast signals, not the unchanged production minute signal.')
 w('CRITICAL: the following low/high columns price an exit at the LOW or HIGH held-side bid anywhere in the execution minute. They are retrospective OHLC stress scenarios, not available quotes at the trigger, not executable fill estimates, and not statistical confidence intervals. No depth or within-minute quote order is known. They demonstrate why exact all-history fast P&L remains unidentified. Missed real fills are unknown, not zero.')
 for k in ['drop_2_g0_p0','drop_1_g0_p0','drop_2_g120_p60']:
  w('Rule '+k)
  rows=[]
  for i in [1,10,30,60]:
   r=next(r for r in fast if r['id']==f'i{i}_{k}');l=r['low'];h=r['high'];rows.append([i,f"{l['losses_reduced']}–{h['losses_reduced']}",f"{l['winners_cut']}–{h['winners_cut']}",money(l['recovered']),money(l['winner_cost']),money(l['net']),money(h['net'])])
  table(['Checks seconds','Losses reduced low/high','Winners cut low/high','Recovery at low','Winner cost at low','Low-bid net','High-bid net'],rows)
 w('Per-asset fast results below use the same immediate2pointdrop, no holding or confirmation delay. These are price-envelope scenarios with the same caveat, NOT full-depth backtests.')
 for a in ASSETS:
  w(a);table(['Seconds','Losses reduced low/high','Winners cut low/high','Hold','Low net','High net'],[[i,f"{r['assets'][a]['low']['losses_reduced']}/{r['assets'][a]['high']['losses_reduced']}",f"{r['assets'][a]['low']['winners_cut']}/{r['assets'][a]['high']['winners_cut']}",money(r['assets'][a]['low']['hold']),money(r['assets'][a]['low']['net']),money(r['assets'][a]['high']['net'])] for i in [1,10,30,60] for r in fast if r['id']==f'i{i}_drop_2_g0_p0'])
 w('Original/premium fast splits, timing bands,5ctnormalization, frozen-versus-rolling volatility, directCF/anchoredCF, source delays1/3/10s and unchanged-minute controls are in diagnostics.json and signal_sensitivity.json. Price-drop fast rules use the lastcompleted-minute bid, because intraminute bid triggers cannot be invented.')
 w()
 w('ACTUAL RECORDED DEPTH:20TRADES,18WINS,2LOSSES')
 w('Continuous sequence-checked books and source first-arrival timestamps exist for the prior BTC/ETHOct3–4 cohort.5contracts,1s execution latency,worse before/after held-side depth,fees+1c friction. Reproduced256 prior cases and expanded to1,280exitsettings. One earlier winning entry starts before complete capture and remains explicitly excluded from both matched comparisons.')
 table(['Checks seconds','Losses reduced','Winners cut','Recovery','Exitfees','Friction','Net'],[[i,r['metrics']['losses_reduced'],r['metrics']['winners_cut'],money(r['metrics']['recovered']),money(r['metrics']['exit_fees']),money(r['metrics']['friction']),money(r['metrics']['net'])] for i in [1,10,30,60] for r in depth if r['id']==f'i{i}_drop_2_g0_p0'])
 w('Matched holdbaseline−2.8977;1s result+1.8475=−2.8977+4.7452. Neither loss becomes profitable. Both rescued BTC entries hadz≈2.44/2.41 and fail today’s originalBTCz3. This small result cannot justify changing the current bot. Preserved previous full-current-entry matched replay has14winners, no losses and no benefit from this wide stop.')
 bc=load('book_coverage_audit.json');w(f"Other archives audited:315,432 older snapshots end before this bot launched, zero actual-holding overlap. Restingpaper has partialbooks for{len(bc['resting_observer_partial_coverage'])} actualtrades, includingCADloss; atmost10.47s, not full holding paths. Entryobserver stores short-lived entry-side depth, not continuous exit-side depth. These cannot fill the missing all-history sellbook gap.")
 w()
 w('ENTRY + EXIT PORTFOLIO RESULTS — SEVEN CURRENTLY ACTIVE ASSETS')
 w('All results here are MINUTE-QUOTE execution proxies with5ct, fees and1c/contract perentry/perexit friction, not observed live fills. Current means TODAY’s entry profiles replayed historically, includingBTCz3. It is a different baseline from the actual+$33.1242ledger. Paired z1.8 means original1.8/premium2.8, constant; does not preserve the adaptive FXprofile. These broad changes are research only.')
 selected=['active7/current/hold','active7/current/drop_2_g120_p60','active7/current/fit_selected','active7/paired_z1.25_t1_10/drop_2_g120_p60','active7/paired_z1.5_t1_10/hold','active7/paired_z1.5_t1_10/drop_2_g120_p60','active7/paired_z1.8_t1_10/hold','active7/paired_z1.8_t1_10/drop_2_g120_p60','active7/paired_z2_t1_10/drop_2_g120_p60','active7/fit_best/fit_selected','active7/XRP_only_z1.5_t3_10/hold']
 table(['Design','EarlierSep12–Oct7 net','LaterOct8–14 trades','Later/day','Later losses','Later net','Later drawdown','Net with2cfriction','Net with5cfriction'],[[r['name'],money(r['periods']['fit']['net']),r['periods']['later']['trades'],f"{r['periods']['later']['trades_per_day']:.2f}",r['periods']['later']['losses'],money(r['periods']['later']['net']),f"{r['periods']['later']['drawdown']:.4f}",money(r['later_friction_2c']['net']),money(r['later_friction_5c']['net'])] for name in selected for r in port if r['name']==name])
 w('Important interpretation: original1.5/premium2.5 plusgentlestop gives590latertrades/+55.9580versus221/+29.4793currenthold. But its earliernet+32.2678versuscurrent+146.4495, and olderprelaunch−45.8094, reject it as a broadly reliable improvement. z1.8isnotaspecialsolution:256latertrades,hold−.8462,stop+7.7449;2cfrictionwithstop−6.2021. Earlier-data best-config selection deteriorates to+2.5345later and−36.8015at2cfriction.')
 w('XRP-only1.5/2.5 in3–10m is a narrower exploratory frequency candidate:296latertrades/+35.0122versus221/+29.4793, but earliernet143.7577versus146.4495anddrawdown20.9597versus12.4612. Thus even this is not a clean improvement. Preserve as research, not a live recommendation. Existing EURUSD lower-z research remains separately deferred as requested.')
 w('Increased frequency numbers are calendar-day modeled-opportunity rates over the shown covered period, not forecasts of live filled trades. No unconditional guarantee that five contracts were offered at a minute quote. Slippage/risk brakes can change the selected trade set, so stress P&L is recomputed, not simply a fixed subtraction.')
 w()
 w('PAUSED LANES')
 for a in ['COPPER','USDCAD']:
  r=sel[a]['current'];w(f"{a}: actual trades{chosen['assets'][a]['trades']}, actualnet{money(chosen['assets'][a]['hold'])}. Current-entry quoteproxy{r['trades']}modeledtrades, net{money(r['full_hold'])}; earlier{r['fit_trades']}trades/net{money(r['fit_hold'])},laternet{money(r['later_hold'])}. Earlier-data exit choice={r['exit_id']}.")
 w('Copper’s favorable broad minute replay already conflicts with live losses; a stop that saves neither of its actual losses does not close that validation gap. USD/CAD has only3earlier-period qualifying modeledtrades and oneactual loss, insufficient independent validation. The short CADholding period rules out longholding-delay exits; tighterfaststops require missing sellbook evidence. Both remain paused.')
 w()
 w('EVERY ACTUAL LOSS — HOLD, GENTLE MINUTE EXIT, IMMEDIATE2POINT1S OHLC SCENARIOS')
 audit=[]
 for r in actual:
  if r['won']:continue
  t=r['ticker'];m=next(x for x in ml['drop_2_g120_p60'] if x['ticker']==t);l=next(x for x in dets['i1_drop_2_g0_p0']['low'] if x['ticker']==t);h=next(x for x in dets['i1_drop_2_g0_p0']['high'] if x['ticker']==t)
  audit.append(dict(ticker=t,asset=r['asset'],lane=r['lane'],quantity=r['quantity'],entry_z=r['entry_z'],entry_gate=r['entry_gate'],holding_seconds=r['close']-r['entered'],entry_cost=r['cost'],entry_fees=r['entry_fee'],hold_net=r['hold_net'],minute_exit=m,fast_low=l,fast_high=h))
 table(['Market','Lane','Qty','Entryz','Holdseconds','Holdnet','Gentleexitnet','1slow/highnet','1strigger'],[[r['ticker'],r['lane'],r['quantity'],f"{r['entry_z']:.3f}",f"{r['holding_seconds']:.1f}",money(r['hold_net']),money(r['minute_exit']['net']),money(r['fast_low']['net'])+'/'+money(r['fast_high']['net']),r['fast_low']['trigger']] for r in audit]);save('all_22_loss_audit.json',audit)
 w()
 w('COVERAGE, CAUSALITY AND VERIFICATION')
 w('Seventeen missing minute-source observations across two eventual winners remain visible; those check times cannot trigger an exit. All22losses have minute coverage; all459actualpositions have complete second-source paths. Archived61point source-stat receipts supplement rawminute gaps; provenance infeature_fallback.json. No fabricated interpolation. Minute exitquotes cover modeledfirsttriggers; fullfivecontractdepth is unverified.')
 w('Minute execution uses the worse of signal-boundary heldbid and following-minute openingbid, with1s modeleddecision delay. Entry uses worse(signalask,nextminuteopeningask),rejectsoutsidefloor/cap andspread>2c. The following opening quote has no precise historical first-arrival timestamp; proxy assumptions are explicit. A first exit with missing/zero bid is held in quoteproxy, not labeled successfully sold. Fast sensitivity cannot change this book limitation.')
 w('Opportunity grid is firstentry/oneowner/market. Portfolio locks the asset while held and accounts for recorded settlement time; it does not recreate the productionIOCnetwork path or settlement-account polling delays. This prevents claiming simulated signals as guaranteed fills. Lowerentrythreshold/newentry results cannot be inferred by filtering actualfilled ledger z.')
 w('EarlyperiodSept12–Oct7; laterOct8–14; olderperiodbeforeSept12reportedseparately. Sourcehistoriesstartatdifferentassetlistingdates. Reusing previously researched history and tryingmanyparameters creates selection bias. Later is a chronological check, not pristine prospective validation. Bootstrapintervals are exploratory and includezero for thegentleactualexit.')
 w('Fee model uses7%*quantity*p*(1-p), sixdecimalceil and fourdecimalcash rounding; actualentryledger fees retained. Alltestedmarketmultipliers ininput manifest. References: https://docs.kalshi.com/getting_started/fee_rounding ; https://docs.kalshi.com/api-reference/market/get-market-candlesticks ; https://docs.kalshi.com/cfbenchmarks/rest-passthrough . Candlesticks supportminuteperiods, nothistorical1sfullbooks.')
 w('Audit: '+json.dumps(load('audit_results.json')))
 w('17unit tests pass. Decimalfee cases, thresholdsign/persistence/missingresets, scalar/vectorparity, cashconservation/riskconstraints, priorrawdepthreproduction allchecked. Live preservation receipt: preservation_after.json. Fullresults, scripts, inputmanifests, allfailedvariants, andlossreceipts retained local/server. Stablecheckpoint preserved.')
 import re
 cleaned=[]
 fixes={'minimumhold':'minimum hold','exitfee':'exit fee','entryfee':'entry fee','totaldelta':'total delta','earlydelta':'early delta','laterdelta':'later delta','currenthold':'current hold','gentlestop':'gentle stop','cashsplit':'cash split','withinshared':' within shared','fullfivecontractdepth':'full five-contract depth','firstentry':'first entry','minutequotes':'minute quotes','lastcompleted-minute':'last completed-minute','CFhour':'CF hour','sourcehistory':'source history','old_overlap':'old_overlap','perentry':'per entry','perexit':'per exit','holdbaseline':'hold baseline','actualtrades':'actual trades','afterfees':'after fees','entryledger':'entry ledger','actualentryledger':'actual entry ledger','fullresults':'full results','inputmanifests':'input manifests','checkpoint':'checkpoint'}
 for line in lines:
  if ' | ' not in line and not line.startswith('Rule ') and 'https://' not in line:
   for a,b in fixes.items():line=line.replace(a,b)
   line=re.sub(r'(?<![\w./])([A-Za-z]+)([0-9])',lambda m:m[0] if m[1]=='z' else m[1]+' '+m[2],line)
   line=re.sub(r'(?<![\w./])([0-9]+(?:\.[0-9]+)?)([A-Za-z]+)',r'\1 \2',line)
   line=re.sub(r',(?=[A-Za-z])',', ',line)
  cleaned.append(line)
 (P/'REPORT.txt').write_text('\n'.join(cleaned)+'\n');print('report lines',len(lines),'loss audit',len(audit))
if __name__=='__main__':main()
