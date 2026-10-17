"""Present the complete fixed-cohort research without claiming actual fills."""
import datetime as dt
import json,gzip
from pathlib import Path
from zoneinfo import ZoneInfo
P=Path(__file__).resolve().parent;ET=ZoneInfo('America/New_York')
g=json.loads((P/'grid_results.json').read_text());touch=json.loads((P/'price_touch_results.json').read_text());sel=json.loads((P/'selected.json').read_text());sens=json.loads((P/'selected_sensitivity.json').read_text());led=json.loads((P/'selected_ledgers.json').read_text());aud=json.loads((P/'independent_audit.json').read_text())
lines=[]
def w(s=''):lines.append(s)
def dollars(v):return f'{v:+.4f}'
def metrics(m):return f"{m['adds']} | {m['add_wins']}W/{m['add_losses']}L | {m['extra_winner_profit']:.4f} | {m['extra_losses']:.4f} | {dollars(m['extra_net'])} | {dollars(m['total_net'])} | {dollars(m['total_net_friction'])}"
w('WTI ADD5 ON DOWNTURNS —20/30/40/50c AND SIGNED z/TIME')
w('Exact frozen cohort:106winning and5losing trades,111total, Oct8–14 2026ET.')
w('Original5contracts remain held. At most one additional5-contract purchase on the SAME outcome, then hold both to settlement. All111trades retained including no-add trades. Baseline actual net+$15.3050 after initial entry fees. No live changes.')
w()
w('FINDING')
w('Blind additions have a small positive modeled full-sample result, but lose on the later Oct12–14 segment. The most interesting fitted candidate is40c with signed z>=-0.5 and60<seconds remaining<=180:4winning adds and2losing adds; extra+$7.4960 after fees, combined+$22.8010 versus+$15.3050holding.1c/added-contract friction lowers combined to+$22.5010. Three-second order latency retains all6adds;2c friction gives+$22.2010.')
w('This is a research candidate, not a validated live edge: sixadds selected from384variants on previously inspected outcomes, only two additions in the earlier segment, no paired historical order-book depth or source first-arrival proof. Requiring z>=0 instead eliminates all four recovered winners at40c and retains one loss in that time window. The z boundary is sensitive, not a broadly validated threshold.')
w()
w('STAGE1: BUY-ASK TOUCH ARITHMETIC, ASSUMING ALL5FILLED AT CAP')
w('Price | additions | addedW/L | extra winner profit | extra losing-trade loss | extra net after fees | combined net after fees | combined with1cfriction')
for cap in [20,30,40,50]:w(f'{cap}c | '+metrics(touch[str(cap)]['full']))
w('These are potential price-touch returns, not simulated execution proof. A40c selling BID is insufficient: the held-side BUY ask must reach40c. Thus the earlier8winners with bids<=40c become only5winners with buying asks<=40c. Only7of106winners have asks<=50c; all5losers do. No premium-lane winner reaches these buy prices.')
w()
w('STAGE1B: CHRONOLOGICAL SUBSEQUENT-BUY-PRINT PROXY (PRIMARY COMPARISON)')
w('Same columns. Includes1second assumed feed receipt lag plus1second order latency; require a distinct subsequent same-side aggressive buy print with>=5contracts at/belowcap within5seconds. Pay cap even when the witness trade is cheaper. This is still a simulated substitute for an order book, not our actual fill.')
for cap in [20,30,40,50]:w(f'{cap}c | '+metrics(g[f'cap{cap}_any_all']['full']))
w('One winning market, KXWTI15M-26OCT081545-45, is counted in40/50c touches but fails the subsequent-print test. Its cheap aggressive buys cluster near15:44:54ET; after the assumed feed+order delay there is no qualifying subsequent>=5contract buy print inside the deadline. We do not credit that winner merely because a low price printed.')
w('Fees are included once for each additional purchase. No exit fee is charged because additions settle.5contracts at20/30/40/50c cost including fees1.0560/1.5735/2.0840/2.5875. An added winner earns3.9440/3.4265/2.9160/2.4125. An added loser loses its full added cash outlay. Fee-only break-even conditional win rates21.12%/31.47%/41.68%/51.75%; the original106/111win rate is not the relevant conditional rate.')
w()
w('STAGE2: BEST FULL-SAMPLE z/TIME RULE AT EACH CAP — RETROSPECTIVELY SELECTED')
for cap in ['20','30','40','50']:
    key=sel[cap]['full_hindsight_choice'];c=sens[key]['config'];w(f"{cap}c {c['zfilter'][0]}, window{c['window'][0]} | "+metrics(g[key]['full']))
w('Exact windows: last60 means0<remaining<=60seconds;60to180 means60<remaining<=180seconds. z_ge_-0.5 means signed cushion at least minus0.5, not a0.5drop from entry and not absolute z. Negative z means the underlying has crossed the original entry reference against the held outcome, measured in the frozen-volatility scale. No new initial entry thresholds are changed.')
w()
w('40c COMPARISON — SAME EXECUTION MODEL')
w('Filter | addedW/L | extra net after fees | combined net after fees')
for z,window in [('any','all'),('any','60to180'),('z_ge_-2','60to180'),('z_ge_-1','60to180'),('z_ge_-0.5','all'),('z_ge_-0.5','gt60'),('z_ge_-0.5','60to180'),('z_ge_-0.5','last60'),('z_ge_0','60to180')]:
    m=g[f'cap40_{z}_{window}']['full'];w(f"{z}/{window} | {m['add_wins']}W/{m['add_losses']}L | {dollars(m['extra_net'])} | {dollars(m['total_net'])}")
w()
w('SIX ADDS FOR40c / z>=-0.5 /1–3MINUTES')
key='cap40_z_ge_-0.5_60to180'
for x in led[key]:
    if x['added']:
        stamp=dt.datetime.fromtimestamp(x['fill_time'],ET).isoformat();w(f"{x['ticker']} | {'WIN' if x['won'] else 'LOSS'} | {stamp} | remaining{x['remaining']:.3f}s | signalz{x['signal_z']:.6f}/recheckz{x['fill_z']:.6f} | observedbuy{x['execution_print_price']*100:g}c,{x['execution_print_qty']:g}ct | modeledcap40c | extra{dollars(x['extra_net'])}")
w('Compared with price-only40c, this avoids additional purchases on the Oct8 20:15, Oct9 18:45 and Oct13 19:15 losing markets, keeping the same4modeled winning adds. Original losses remain; their losses are not rescued, simply not enlarged by an additional purchase. It still adds to the Oct12 14:30 and Oct14 21:30 losses.')
w()
w('CHRONOLOGICAL SPLIT: EACH RULE FROZEN ACROSS SEGMENTS')
w('Earlier Oct8–11:48trades46W2L. Later Oct12–14:63trades60W3L. This is NOT an untouched holdout: both periods were previously inspected in exit research.')
w('Rule | earlier addedW/L,extra net | later addedW/L,extra net')
for key in [f'cap{c}_any_all' for c in [20,30,40,50]]+[sel[str(c)]['full_hindsight_choice'] for c in [20,30,40,50]]:
    a,b=g[key]['development'],g[key]['later'];w(f"{key} | {a['add_wins']}W/{a['add_losses']}L,{dollars(a['extra_net'])} | {b['add_wins']}W/{b['add_losses']}L,{dollars(b['extra_net'])}")
w('Predeclared development selection required>=5earlier additions.20/30/40c have NO eligible configurations; do not call their later results validation of a development-selected rule.50c has8eligible variants; its earlier-selected rule is no z/time filter and later extra PnL is-$2.9375. The full-sample40c candidate was selected using all outcomes and had just2earlier adds. This prevents a clean out-of-sample edge claim.')
w()
w('EXECUTION AND FRICTION SENSITIVITY')
w('Rule | standard total |3second order latency total/WL |2cfriction total | unadjusted day-bootstrap95% extra-with1cfriction')
for key in [f'cap{c}_any_all' for c in [20,30,40,50]]+[sel[str(c)]['full_hindsight_choice'] for c in [20,30,40,50]]:
    x=sens[key];m=x['latency3s']['full'];w(f"{key} | {dollars(x['summary']['full']['total_net'])} | {dollars(m['total_net'])}/{m['add_wins']}W{m['add_losses']}L | {dollars(x['friction2c_total'])} | {x['uncertainty']['bootstrap95_total_extra_friction']}")
w('Bootstrap resamples7calendar days including no-trade days,10,000draws; intervals are descriptive and NOT adjusted for384rule searches. They do not establish an edge.50c price-only changes from+$1.5375extra to-$0.8750extra under3second order latency because a winning add disappears.')
w()
w('CAPITAL AND EXISTING LIMITS')
for key in [f'cap{c}_any_all' for c in [20,30,40,50]]+[sel[str(c)]['full_hindsight_choice'] for c in [40,50]]:w(key+' '+json.dumps(sens[key]['risk'],sort_keys=True))
w('An added5contracts doubles that market position to10contracts and commits up to$2.5875extra including fees. These fixed-cohort comparisons preserve every actual initial entry and do not model displaced entries in other assets, shared daily risk or portfolio budget contention. Peak WTI-only contracts are recorded above; the live25contract/$25/$20shared limits are unchanged. The modeled gains cannot be assumed deployable under those shared limits without a full portfolio replay. Turnover is cumulative cash spent, not simultaneous bankroll.')
w()
w('REPRODUCTION, DATA, AND LIMITATIONS')
w('Capture:35publicAPIpages,29,652deduplicated non-block trade receipts across12markets, all pagination cursors exhausted.99other cohort markets never show a buy ask<=50c in overlapping held-period minute candles, so no cheaper-cap add is credited there. All111rows retained in every scenario. Cached one-second underlying history is used only from timestamps available before the modeled decisions; entry reference and volatility are fixed. Historical sources may have corrections and original first-arrival times are unavailable; one-second arrival is an assumption.')
w('BUY side: YES uses yes_ask; NO uses1-yes_bid. Legacy taker_side identifies purchased outcome in public records. A subsequent print is another trader\'s completed transaction and may consume all available liquidity; its size is not proof our additional order would fill. There is no archived contemporaneous full-five-contract depth for these adds. No fake zero-slippage or perfect low-price entry: modeled additions pay cap and include taker fees. Tick-level evidence improves chronology, but remains a counterfactual execution proxy.')
w('Fees:0.07*5*p*(1-p), six-decimal ceiling and four-decimal cash debit ceiling, from verified quadratic/multiplier1WTI metadata.1c/2cfriction stresses are separate additional cost, not exchange fees. References: https://docs.kalshi.com/api-reference/market/get-trades ; https://docs.kalshi.com/getting_started/fee_rounding')
w('Independent audit: '+json.dumps(aud,sort_keys=True))
w('Files: PLAN.txt;capture.py/public/*.json.gz;trades.json.gz;candidate_markets.json;analyze.py;price_touch_results/ledgers.json;configs.json;grid_results.json;grid_ledgers.json.gz;selected_sensitivity.json;selected_ledgers.json;audit.py/independent_audit.json;production preservation snapshots. Original frozen111trade input hashes retained. No orders, live imports, config edits or restarts. Research artifacts only.')
(P/'REPORT.txt').write_text('\n'.join(lines)+'\n')
print('Report saved:',P/'REPORT.txt')
