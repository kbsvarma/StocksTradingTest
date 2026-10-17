from pathlib import Path
import json,collections
P=Path(__file__).resolve().parent
def read(n):return json.loads((P/n).read_text())
def row(label,v,frequency='trades_per_day'):
 return f"{label} | {v['trades']} | {v.get('wins','-')}/{v.get('losses',v['trades']-v.get('wins',0))} | {v.get(frequency,0):.2f}/day | ${v['net']:+.4f} | DD${v['drawdown']:.4f}"
lines=['CROSS-MARKET KALSHI RESEARCH — 2026-10-11','Research simulations, not live account P&L. Existing bot unchanged.','',
'DECISION: adaptive tennis is the new candidate worth advancing to independent paper/forward validation. Do not claim a verified live edge or deploy from this study. The frozen30s-source rule made money in all three date blocks and stayed positive in combined execution stresses. It averaged3.93/day over28days and5.43/day in the latest week; activity is uneven. Its descriptive95% P&L interval still includes zero, even before correcting for18tennis parameter cells searched.',
'The90s-source variant is stronger on aggregate, but it changes the score state and therefore the bets: it is not merely the same orders filled later. Treat its8.07/day and+$17.06 as another exploratory signal variant. Its latest-week combined stale-source+extra-minute+2c-cost case loses$0.4130. No claim of an inevitable or proven latency-resistant profit.',
'Next validation must timestamp first-received points, reconcile corrections/out-of-order events, record depth at the actual intended quantity, and price paper orders against subsequent books using the frozen rules. An independent sample is necessary before allocating the$100 live. Existing production bots and risk settings remain unchanged.','',
'Scope: sports contract routing and all-NO baskets; ordered financial-strike baskets; eight-city external-observation weather signals; three lower-fee metal-perpetual entry families; fixed and adaptive score-state tennis probabilities. These are additional to the previously rejected BTC/ETH paired trade and sports discounted-entry studies.',
'Binary replays use one contract with a shared$100 bankroll,25contract/$25 exposure and$20 daily risk including open debit. This is not$100 allocated independently to each position. Quote-based assumed fills, not confirmed historical orders.',
'Binary cost: 0.07*series_multiplier*p*(1-p), rounded upward to$0.0001, plus1c/contract base or2c stress. Current normalized fee model may differ from fees charged historically. No maker rebates. Entry uses the worse of the decision ask and subsequent minute opening ask, with signal+2c cap; an extra-minute delay uses next-minute closing ask.',
'Volume is an activity filter, not proof of executable depth. Candles and revised play-by-play do not prove first-received live timestamps. Neither profitable replays nor mathematical tests establish a production edge.','',
'TENNIS: fixed exploratory lead','Rule: ATP+WTA best-of-three singles; infer serve strengths from pre-match odds; update with100-point prior per player and observed serve points; half-shrink model probability toward market midpoint; demand6c residual edge after fees and1c friction.10–90c entry quotes, spread<=5c, prior10minute volume>=5. One contract/match; hold settlement. Evaluate every minute. No league chosen by P&L.',
'This lead was chosen after18 tennis cells were examined. Its older-fortnight rule was frozen before older data was downloaded. The initial recent data is NOT an untouched holdout, and the additional older check does not eliminate selection bias.',
'Splits use actual entry dates. Initial selection excludes outcomes unsettled at October3 00:00ET. Scheduled-start-date results that counted eight entry dates in a seven-day interval were corrected and superseded. Initial hard5/day-in-every-stress selection did not select this lead; it is explicitly a follow-up candidate, not a claimed passed gate.','',
'Period/scenario | Trades | W/L | Frequency | Net after fees+friction | Realized drawdown']
pref='blend0.5/edge0.06/'
for filename,period,title in [('tennis_older_results.json','older','Sep12–25'),('tennis_adaptive100_results.json','selection','Sep26–Oct2 (purged)'),('tennis_adaptive100_results.json','later','Oct3–9')]:
 data=read(filename)
 for scenario,cost in [('lag30/base',1),('lag30/base',2),('lag30/delay60',1),('lag30/delay60',2),('lag90/base',1),('lag90/delay60',2)]:
  key=pref+scenario+'/'+period+f'/cost{cost}'
  if key in data:lines.append(row(title+' '+scenario+f' cost{cost}c',data[key]))
lines+=['','Combined continuous$100 replay Sep12–Oct9:']
for k,v in read('tennis_lead_combined_results.json').items():
 lines.append(row(k,v));lines.append(f"  Remove best3: ${v['without_best3']:+.4f}; descriptive2-day-block95% total P&L interval: {v['descriptive_block_bootstrap_total_95']}; fees${v['fees']:.4f}, friction${v['friction_total']:.4f}; min free cash${v['skips']['min_cash']:.4f}.")
lines+=['These intervals are NOT adjusted for searching variants. Cash-flow realized drawdown understates possible intramatch mark-to-market drawdown. The combined replay includes boundary-crossing trades omitted from the purged selection table, so its count need not equal that table sum.','',
'OTHER STRATEGIES: comparable later-period checks','Period Oct1–9; perps prior selection Sep10–30. Binary routing prior selection Sep12–30 plus Aug15–Sep11 diagnostic.']
routes=read('route_results.json')
for timing in (5,30):
 for period in ('selection','later'):
  lines.append(row(f'Sports favorite cheapest route/pre{timing}/{period}',routes[f'favorite/pre{timing}/best_route/base/{period}/cost1']))
for family in ('short_fade/60/2','vwap_fade/60/1','vwap_pullback/60/0'):
 for period in ('select','validate'):
  d=read('perp_'+period+'_results.json')
  for mode in ('base','slip5','delay120'):
   lines.append(row('Metal perps '+family+'/'+period+'/'+mode,d['POOL/'+family+'/'+mode],frequency='trades_per_calendar_day'))
lines+=['Perpetuals: four eligible assets GOLD/SILVER/PLATINUM/PALLADIUM; fixed$25 subaccount allocation each, aggregate$100. Newer copper/aluminum/index contracts lacked five prior sessions and were excluded by coverage, not outcome. Aggregate notional capped near$100, half-margin at2x isolated leverage; this is not a2x total-account directional bet.',
'Perp net includes bid/ask spread, recorded funding, current0.72bp/side taker rate and1bp/side friction; stress5bp/side. Sixty-second decisions-to-fills,120-second stress, integer quantity,1% risk per suballocation,20% cash reserve. Actual historical margin/depth not captured; current leverage metadata approximates margin. Entry-minute reported volume is only a fill-activity proxy. VWAP here means volume-weighted quote midpoint, not true trade-price VWAP. Missing joint marks can understate mark-to-market drawdown.',
'The representative perp rows do not hide the full grid: APPENDIX.txt contains all12configs. None survived positive prior-period cost/delay tests at>=5/calendar day. Raising leverage does not repair their net expectancy.','',
'CROSS-CONTRACT BASKETS',
f"Screened {sum(v for k,v in read('structure_counts.json').items() if k.endswith('_events'))} event records:8085sports and1109financial expiry ladders. Zero qualifying positive-cost-adjusted baskets in the sampled quotes. Sports scans are30–5minutes before start; financial scans use cached minute tapes. This is not a claim that no sub-minute arbitrage ever exists.",
'Sports all-NO baskets rely on at-most-one winner; ordered strike YESlow+NOhigh relies on identical outcome definitions and expiry. Cancellation/fair-value settlement can break simple bounds and remains a contract-specific consideration. No inferred fills from last trade price.','',
'WEATHER OBSERVATION BOUNDS',
'448 city-days, eight cities Aug15–Oct9. IEM airport temperatures,10/30minute publication lag,2F/3F buffer. Every5minutes09:00–20:00local, plus original noon/16ET screen. Zero qualifying trades. For lag10/buffer2,32065 bound-matching contract/time observations:26272 lacked a usable quote;5793 had an ask outside1–<98c. Missing quotes are not proof of absent opportunities.',
'Settlement source is The Weather Company, not the airport archive. Across448 days observed maximum minus settlement ranged−8F to+3F; one day exceeded2F, none exceeded3F. The cross-source bound is therefore a signal, not guaranteed settlement. Final archives are not as-of reception logs.','',
'REPRODUCTION AND EVIDENCE','See REPRODUCE.txt, all JSON result grids and compressed ledgers.8 meaningful scoring/accounting unit tests; audit.json records independent chronology, fee identities, duplicate ownership and bankroll assertions. Counts include repeated stress scenarios, not independent trades.',
'Primary references:','https://docs.kalshi.com/getting_started/fee_rounding','https://kalshi.com/docs/kalshi-fee-schedule.pdf','https://docs.kalshi.com/api-reference/historical/get-historical-markets','https://mesonet.agron.iastate.edu/request/download.phtml','https://www.itftennis.com/en/about-us/governance/rules-and-regulations/']
(P/'REPORT.txt').write_text('\n'.join(lines)+'\n')
app=['ALL FIXED GRID CELLS — no live P&L, base1c binary /1bp-side perp friction','Tennis: selection Sep26–Oct2 purged; later Oct3–9. Frequency denominator7calendar days.']
for source in ('tennis','tennis_adaptive40','tennis_adaptive100'):
 d=read(source+'_results.json');app.append('\n'+source)
 for k,v in sorted(d.items()):
  if '/lag30/base/' in k and k.endswith('/cost1'):app.append(row(k,v))
app.append('\nMetal perps: selection21calendar days; later9calendar days')
for mode in ('select','validate'):
 d=read('perp_'+mode+'_results.json')
 for k,v in sorted(d.items()):
  if k.startswith('POOL/'):app.append(row(mode+'/'+k,v,frequency='trades_per_calendar_day'))
app.append('\nSports routing')
for k,v in sorted(routes.items()):
 if '/base/' in k and k.endswith('/cost1'):app.append(row(k,v))
(P/'APPENDIX.txt').write_text('\n'.join(app)+'\n')
