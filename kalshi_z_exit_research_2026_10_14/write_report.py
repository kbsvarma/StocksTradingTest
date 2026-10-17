import json,gzip
from pathlib import Path
P=Path(__file__).resolve().parent
a=json.loads((P/'analysis.json').read_text());res=json.loads((P/'results.json').read_text());coverage=json.loads((P/'coverage.json').read_text())
lines=[]
def say(t=''):lines.append(t)
say('Z-DETERIORATION EXIT BACKTEST — October14,2026')
say('Research only. Live/paper bots unchanged; USD/CAD remains paused.')
say()
say('DECISION')
say('Do not deploy a blanket z stop. Small drops cut too many eventual winners. The narrow candidate to investigate further is an entry-z decline of more than2 points, a minimum120-second holding delay, and2 consecutive completed-minute breaches. It helps pooled older and recent samples but is not robust enough for live use. With today’s entry design its modeled gain is only$0.3979 across214 entries; costs and different periods can erase it.')
say('Example: entry z2 -> exit threshold below0; entry z3 -> below1; entry z4 -> below2. This is a fixed drop from ENTRY z, not from its later peak and not a fixed z1.5 stop. Measure z on the held side even if the favorite flips.')
say()
say('RECENT ACTUAL ENTRIES, HYPOTHETICAL EXITS')
say('Oct8 00ET through Oct14 20ET inclusive. Seven currently enabled assets; five contracts each.235 complete-path trades:224 eventual winners,11 losers. Their actual hold-to-settlement net after charged entry fees was$26.7113. Copper excluded because retired; paused USD/CAD reported separately. One additional WTI winner+$0.1678 has incomplete archived pre-open source history and is excluded from BOTH sides of the matched headline comparison, retained in every raw dataset. Thus236 captured active-asset trades earned$26.8791 in total, but only235 support the complete-path comparison.')
say('Exit proceeds use the lower of the trigger-minute closing held-side bid and subsequent-minute opening bid, minus1c/contract slippage and rounded down to market ticks. Actual entry costs/fees are preserved; modeled taker exit fees and sale cash rounding are added. These are quote proxies without five-contract exit-depth proof, not actual executed sells.')
say('Rule | Losses reduced (of11) | Winners exited (of224) | Winners turned losses | Net after fees/slippage | Change vs hold')
say('Hold to settlement |0|0|0|$26.7113|$0')
names=['gate_drop_0.5_wait0_persist1','entry_drop_0.5_wait0_persist1','entry_drop_1_wait60_persist2','entry_drop_1.5_wait120_persist1','entry_drop_2_wait120_persist2','entry_drop_2_wait180_persist2']
for name in names:
 r=next(x for x in a['matched_grid'] if x['config']['id']==name);c=r['config'];m=r['recent']
 anchor='entry-required gate' if c['family']=='gate_drop' else 'actual entry z'
 say(f"Drop>{c['level']:g} from {anchor};wait>={c['delay']}s;{c['persist']} consecutive checks |{m['losers_improved']}|{m['winners_cut']}|{m['winners_turned_losses']}|${m['net']:.4f}|${m['delta']:+.4f}")
say('No shown variant fully converted a settlement loser into a nonnegative trade: fully avoided net losses=0. A reduced loss still loses money. The objective achieved in favorable simulations is recovering part of otherwise lost principal, not creating wins.')
say()
candidate='entry_drop_2_wait120_persist2';chosen='entry_drop_2_wait180_persist2'
say('TWO-MINUTE CANDIDATE: WHERE THE RECENT GAIN COMES FROM')
v=a['candidate_checks'][candidate]['recent_complete'];m=v['base']
say(f"Saved${m['loser_savings']:.4f} across7 losing trades; sacrificed${m['winner_profit_sacrificed']:.4f} on1 eventual winner; net improvement${m['delta']:.4f}.224 eventual winners become223 held winners plus1 early-exit loser. All11 original losers remain net losing trades, though7 lose less. Net losing trades therefore rise to12 even while total P&L improves.")
say('Asset | Extra net | Losers improved | Winners cut')
for asset,mm in v['assets'].items():say(f"{asset}|${mm['delta']:+.4f}|{mm['losers_improved']}|{mm['winners_cut']}")
say('Trade | Hold net | Stop net | Sale price | Exit z')
for r in v['ledger']:
 if r['exit']:say(f"{r['ticker']}|${r['hold_net']:.4f}|${r['net']:.4f}|{r['exit_price']:.3f}|{r['exit_z']:.4f}")
say()
say('DOES IT SURVIVE TODAY’S ENTRY RULES?')
say('Secondary cohort uses the existing full seven-asset entry replay with original BTC raised toz3:214 modeled entries Oct8–14 11:30ET,206W/8L, original entry fees plus1c friction, shared25/$25/$20 modeled during entry selection. We keep its entries fixed and add only hypothetical exits. This is distinct from pruning the live ledger or counting old BTCz2 losses as today’s opportunities.')
for name,label in [(candidate,'2-point decline,120s delay,2 checks'),(chosen,'2-point decline,180s delay,2 checks')]:
 m=a['candidate_checks'][name]['current_design']['base']
 say(f"{label}: hold${m['hold_net']:.4f} -> stop${m['net']:.4f}, change${m['delta']:+.4f}; {m['losers_improved']} losses reduced, {m['winners_cut']} winner cut. All losses still net negative.")
say('For the120s candidate,5c exit slippage changes the current-design improvement to-$0.5612. Much of the recent actual-ledger benefit was from BTCz2 entries that the existing BTCz3 rule already rejects. It is inappropriate to promise the user+$5.04 of improvement under today’s design.')
say()
say('OLDER CHECK: DO NOT PICK ONLY THE BEST RECENT RESULT')
say('Older current-rule crypto replay, Aug13–Oct2 UTC,754 modeled trades,733W/21L across BTC/ETH/SOL/XRP. Original BTCz3, othersz2, premiumz3; same5-contract fixed modeled entries, their original entry fees/friction retained. Different period/assets from the recent primary cohort; not additive independent live profits.')
for name,label in [(candidate,'120s candidate'),(chosen,'180s early-selected rule')]:
 z=a['candidate_checks'][name]['older_current_crypto'];m=z['base']
 say(f"{label}: hold${m['hold_net']:.4f} -> stop${m['net']:.4f}; change${m['delta']:+.4f}; {m['losers_improved']} losses reduced, {m['winners_cut']} winners cut into losses.")
 for per,mm in z['periods'].items():say(f"  {per}: change${mm['delta']:+.4f}")
say('The120s candidate’s older gain+$2.0244 becomes-$0.6072 with a full-minute execution delay, -$0.3799 with5c exit slippage, and-$1.9080 if entry volatility is held fixed. This is a fragile exploratory result, not a verified durable improvement.')
say()
say('SELECTION AND UNCERTAINTY')
say('All112 configurations were defined before running their results: entry-z drops{.25,.5,.75,1,1.5,2}; entry-required-gate drops{0,.25,.5,1}; absolute z floors{0,.5,1,1.5}; minimum hold{0,60,120,180}s; persistence{1,2} minute checks. All variants retained. No asset-specific parameter tuning.')
say('Initial selection used Oct8–11 only and selected the180s/2-point/2-check rule before evaluating Oct12–14. Its early improvement was+$5.0827 and later+$2.0642, but it failed the older history check. We then evaluated the SAME frozen grid across older inputs; the120s version was the only one positive in both pooled older/current-crypto and recent actual cohorts. That second choice is explicitly exploratory and selected after seeing both sets, not an untouched validation result.')
for key in ['recent_complete','current_design','older_current_crypto']:
 u=a['candidate_checks'][candidate][key]['uncertainty'];say(f"120s candidate {key}: paired calendar-day bootstrap95% interval for total improvement {u['paired_day_bootstrap95']}.")
say('Intervals are not adjusted for112-way selection, reuse of historical data, regime shifts or missing depth; they include zero. The180s recent interval cannot generate future unseen winner reversals and does not override its older losses.')
say()
say('TIMING, INPUT COVERAGE AND SCOPE')
say('Broad-grid triggers use only completed-minute observations. Holding delays are measured from recorded acknowledgement or durable intent; the first evaluation is the next eligible full-minute boundary. Two successive checks add at least one minute after the first breach. Thus a120s minimum hold commonly cannot execute until roughly4 minutes after entry; a180s minimum commonly until roughly5 minutes. These results do not prove a30-second/intraminute stop.')
say('All actual entry quantities are5 contracts. 1,286 post-entry minute observations were built;8 lack a complete61-price source window, all belonging to the one disclosed WTI winner. A direct re-fetch and prior-event lookup did not recover the pre-reopen gap. A request for range2h returned the same1h archive and was not treated as more coverage. Missing inputs are never interpolated or filled using a later outcome.')
say('The paused USD/CAD loss entered with only one minute remaining. There is no subsequent completed-minute observation before expiry; every minute-grid rule still loses its original$4.3896. A faster stop needs separate intraminute source-arrival and executable-bid evidence; this study cannot claim to save it.')
say('Rolling volatility uses60 completed log returns; reference stays at entry’s original reference, and side stays at the held side. Retrieved entry spot matched230/237 captured trades;6 mismatches and1 incomplete entry are retained in coverage.json. Historical source revisions may affect hypothetical post-entry signals. All available noncrypto entry value hashes matched. The source is the historical public archive, not a claim of exact original reception times.')
say('Hypothetical exits change only the outcomes of a fixed entry schedule. We do not add replacement/re-entry trades or claim a complete newly optimized portfolio simulation. Selling can alter subsequent cash and daily-risk eligibility; any implementation requires a full prospective portfolio check. Drawdown shown in machine-readable results is realized exit/settlement-ordered, not mark-to-market.')
say()
say('VALIDATION AND ARTIFACTS')
say('13 focused tests passed. Independent Decimal/chronology audit checked50,512 frozen-grid trade/scenario rows and6,729 exits: unchanged entry costs, strict prior-time z, persistent checks, adverse held-side bids, valid exit-before-close, per-trade fees and exact cashflow. Failed and losing configurations remain in results.json/all_ledgers.json.gz.')
say('PLAN.txt; capture.py/capture_receipt.json; actual_orders.json; raw fresh/ receipts; build.py/paths.json.gz/coverage.json; replay.py/results.json/selection.json; older_check.py/older_results.json/older_paths.json.gz; analyze.py/analysis.json; test_replay.py; audit.py/audit.json. Existing source references remain in kalshi_loss_review_2026_10_14 and kalshi_btc_eth_quant_2026_10_02. No live or paper strategy deployment is part of this research.')
say('Fee specification verified against https://docs.kalshi.com/getting_started/fee_rounding ; candle resolution https://docs.kalshi.com/api-reference/market/get-market-candlesticks .')
(P/'REPORT.txt').write_text('\n'.join(lines)+'\n')
print('Report written',len(lines),'lines')
