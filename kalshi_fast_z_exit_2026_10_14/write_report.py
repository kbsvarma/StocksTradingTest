from pathlib import Path
import json,gzip,datetime,collections
from zoneinfo import ZoneInfo
P=Path(__file__).resolve().parent
def main():
 results=json.loads((P/'results.json').read_text());sens=json.loads((P/'sensitivity.json').read_text());led=json.load(gzip.open(P/'ledgers.json.gz'));timing=json.loads((P/'recent_timing.json').read_text());phase=json.loads((P/'phase_sensitivity.json').read_text());audit=json.loads((P/'audit.json').read_text());raw=json.loads((P/'raw_book_audit.json').read_text());cov=json.loads((P/'coverage.json').read_text())
 lines=['SUBMINUTE Z EXIT RESEARCH — 1, 10, 30, 60 SECOND CHECKS',
 'Research completed October14 ET / October15 UTC, 2026. No live or paper changes.',
 '',
 'Finding: faster monitoring can recover materially more from some losses. In the',
 'matched historical BTC/ETH sample, a two-point drop from entry z with no grace',
 'or persistence recovered $4.7452 at 1s versus $2.0025 at 60s, cutting no winners.',
 'A one-point drop recovered more but cut two winners. This is a small, historical',
 'sample and an experimental fast-feed proxy, not proof of a live-ready exit edge.',
 '',
 '1. Coverage and what the experiment actually changes',
 'Archived sequence-validated raw CF Benchmarks feeds and full order-book streams',
 'cover approximately36.3h Oct2late–Oct4morning;146 accepted captures/292 markets.',
 '21 actual BTC/ETH fills overlap that span. The first ETH decision precedes the',
 'accepted continuous capture, so exclude that winner +$0.4119 on BOTH sides.',
 'Primary matched sample:20 actual5ct entries,15BTC/5ETH,18wins/2losses,Oct3–4.',
 'Actual hold net -$2.8977. All21 overlap net -$2.4858. Never mix these denominators.',
 'Both losses were old original-BTC entries at z2.438592 and z2.414117, below the',
 'current original-BTC z3 gate. These gains cannot be claimed for today’s bot.',
 '',
 'The live z uses completed Coinbase minutes. Polling that unchanged value faster',
 'does not create new information. The fast research proxy anchors CF at the',
 'decision, scales original Coinbase signal spot by subsequent CF relative moves,',
 'retains original pre-open reference and entry minute sigma, and uses fractional',
 'remaining minutes. Primary source: received5Hz CF, <=1.5s source age. This is an',
 'explicit feed/formula variant; it does not establish same-source Coinbase exits.',
 'Sensitivities use1Hz CF, direct unanchored CF price and rolling completed-minute',
 'Coinbase sigma. No conversion of60-second returns into60-minute volatility.',
 '',
 '2. Identical entries and stop rule; only monitoring interval changes',
 'Rule: sell when held-side z < entry z minus2; no initial grace or persistence.',
 'Clock-aligned checks strictly after actual durable-intent/ack time. All20 entries',
 'retained. Original paid costs and fees retained; no re-entry or capital recycling.',
 'Interval | Losses reduced/2 | Winners cut/18 | Loss dollars recovered | Net after fees/friction']
 for i in [60,30,10,1]:
  r=next(r for r in results['actual'] if r['id']==f'i{i}_d2_g0_p0');lines.append(f"{i:2}s | {r['losses_reduced']}/2 | {r['winners_cut']}/18 | ${r['loss_savings']:+.4f} | ${r['net']:+.4f}")
 lines+=['Hold to settlement: -$2.8977. Neither loss turns profitable: these are smaller',
 'losses, not two entirely prevented losing trades. One-second versus60s adds',
 '$2.7427 net in this matched sample; total loss amount falls from$8.9459 to$4.2007.',
 '',
 'Tighter alternative: drop1, no grace/persistence, same20entries:',
 'Interval | Losses reduced | Winner cuts (turned losses) | Recovered | Winner cost | Total net']
 for i in [60,30,10,1]:
  r=next(r for r in results['actual'] if r['id']==f'i{i}_d1_g0_p0');lines.append(f"{i:2}s | {r['losses_reduced']} | {r['winners_cut']} ({r['winners_turned_loss']}) | ${r['loss_savings']:.4f} | ${r['winner_cost']:.4f} | ${r['net']:+.4f}")
 lines+=['The1s/drop1 variant is the best historical grid cell but sacrifices two winners.',
 'It is not selected as a robust optimum from two losses. Do not ignore false exits.',
 '',
 '3. Individual losses: drop2/no grace/no persistence',
 'Market | Hold |1s exit price/time/net |60s exit price/time/net']
 for r in led['actual']['i1_d2_g0_p0']:
  if r['won']:continue
  s=next(x for x in led['actual']['i60_d2_g0_p0'] if x['ticker']==r['ticker'])
  stamp=lambda x:datetime.datetime.fromtimestamp(x,ZoneInfo('America/New_York')).strftime('%m-%d %H:%M:%S.%f')[:-3]+' ET'
  lines.append(f"{r['ticker']} | ${r['hold_net']:.4f} | {100*r['execution']['gross']/5:.1f}c / {stamp(r['book_after'])} / ${r['net']:.4f} | {100*s['execution']['gross']/5:.1f}c / {stamp(s['book_after'])} / ${s['net']:.4f}")
 lines+=['Displayed held-side bid depth supports all5contracts at the cited prices.',
 'Sale price is the worse proceeds across last pre-trigger and first subsequent',
 'book after1s latency, sampled within500ms; not interpolated minute OHLC. Trades',
 'must retain full depth in both books. Deduct level-wise taker fees and an extra',
 '1c/contract friction. Cash rounded down to.0001. These are modeled exits against',
 'recorded depth, not actual executions or guaranteed fills. No partial sale model.',
 'Official rounding reference: https://docs.kalshi.com/getting_started/fee_rounding',
 '',
 '4. Robustness for1s/drop2, same actual20trades',
 'Scenario | Total net | Loss dollars recovered | Winners cut']
 for suffix in ['1hz','direct_cf','rolling_sigma','latency250ms','latency3s','friction2c','friction5c','minute_control']:
  r=sens['actual']['i1_d2_g0_p0_'+suffix]['metrics'];lines.append(f"{suffix} | ${r['net']:+.4f} | ${r['loss_savings']:.4f} | {r['winners_cut']}")
 lines+=['Unchanged-minute control gives identical results at1/10/30/60s for immediate',
 'stops: -$0.8952 fordrop2; -$0.2465 fordrop1. Faster sleep alone is insufficient.',
 'Polling phase sensitivity (all integer-second offsets, drop2):',
 'Interval | Min net | Mean net | Max net']
 for i in [1,10,30,60]:
  x=phase['actual'][f'i{i}_d2'];lines.append(f"{i}s | ${x['min_net']:+.4f} | ${x['mean_net']:+.4f} | ${x['max_net']:+.4f}")
 lines+=['Slow polling outcomes depend strongly on which seconds are sampled. This phase',
 'audit is a post-result robustness check, not a retuned entry/exit rule.',
 '',
 '5. Today’s entry design replay over the matched stream period',
 'Separate existing FULL entry replay, BTCoriginalz3/othercurrentgates:14 modeled',
 'BTC/ETH entries,14wins/0losses, hold+$2.7385 after modeled entryfees+1c friction.',
 'Entry quote replay does not itself prove historical entry depth. Same raw stream',
 'exit checks:1s/drop2 makes no exits and stays+$2.7385;1s/drop1 cuts2winners and',
 'falls to+$0.8019. There is no loss in this small current-entry cohort to rescue.',
 'This is different from pruning historical ledger z values. Do not market the old',
 'BTC loss rescues as a measured benefit to the currentz3 entry design.',
 '',
 '6. Recent noncrypto trigger timing — NO executable exit P&L claimed',
 '161 actual WTI/NATGAS/EURUSD entries from the earlier Oct8–14 audit:156W/5L.',
 'Historical Pyth1s prices, original entry reference/sigma, source assumed visible',
 '1s after timestamp. Actual first-arrival times and subminute bids are unavailable.',
 'This includes the previously excluded WTIwinner whose historical rolling sigma',
 'was incomplete; here entry sigma is frozen. No historical second-price revisions',
 'found in overlapping chart receipts. No missing source checks in primary grid.',
 'Drop2 | Losses flagged/5 | Eventual winners flagged/156']
 for i in [60,30,10,1]:
  x=timing['results'][f'interval{i}_drop2_source_delay1'];lines.append(f"{i}s | {x['losers_flagged']}/5 | {x['winners_flagged']}/156")
 lines+=['These are trigger counts, NOT losses reduced or executed winner cuts. Exit',
 'prices and net cannot be established from the available minute bid records.',
 'Tighterdrop1 at1s flags5losers plus33winners;drop.5 flags62winners. Sensitivities',
 'at source delays3s/5s and every per-trade trigger are retained.',
 'Paused USD/CAD loss separately: drop1/drop2 never triggers before expiry;drop.5',
 'at1s flags19.057s after entry,40s before close with assumed1s source delay.',
 'At3s/5s assumed source delay, that flag appears21.057s/23.057s after entry.',
 'No recorded sell book there, so no defensible dollar recovery claimed. CAD stays',
 'paused. Lowering the stop to rescue this one trade would create many false flags.',
 '',
 '7. Grid, checks and conclusion',
 '256 predeclared combinations percohort:4intervals*4drops*4graces*4persistence',
 'durations, not fixed numbers of checks. All512cell results,8,704trade-scenario',
 'rows and losing variants retained. Worst actualcell i10_d0.5_g30_p60 net-$3.9052.',
 'A2point drop with120s grace and60s persistence at1s only saves1loss/$1.9153,',
 'net-$0.9824: long confirmation waits give up much of the benefit.',
 f"15 offline tests pass. Audit verifies{audit['scenario_rows']}scenario rows/{audit['exits_checked']}exits;",
 f"independent raw-snapshot/delta reconstruction matches{raw['total_books']}selected books.",
 'Across matched actual trades, median CF first-arrival lag39–51ms, worst trade',
 'p99lag103ms, maximum CF receptiongap386ms. Primary20entry source checks have',
 'continuous1s coverage; all fills still simulated. Only two losses, in two days:',
 'no statistical evidence of a persistent net edge, and many tested configurations.',
 '',
 'Research recommendation: continue with a1s monitor of a fresh fast feed and a',
 'wide entry-z minus2 stop as the least destructive candidate in this sample.',
 'It deserves prospective shadow validation with all winners, losses and actual',
 'subsequent5ct bids captured before any live exit change. Test signal freshness,',
 'feed basis, fees, repricing latency and position reconciliation explicitly.',
 'Do not change entry rules or claim that checking once per second guarantees rescue.',
 'No collector deployed or modified in this request; no financial orders sent.',
 'Live source hashes,ARM/HALT flags and all5service states/PIDs unchanged. USD/CAD',
 'remains paused; stable checkpoint retained. No memory of this as a proven edge.',
 '',
 'Artifacts: PLAN.txt;prepare.py/replay.py;inputs.pkl.gz;coverage.json/quality.json;',
 'entries.json;results.json/sensitivity.json/ledgers.json.gz;phase_sensitivity.json;',
 'recent_timing.json/recent_timing_ledgers.json.gz;audit.json/raw_book_audit.json;',
 'test_replay.py/tests.log;preservation_before/after.json. Source streams remain',
 'on192.168.3.136 with SHA256 receipts listed incoverage.json. Research mirrored',
 'at/home/varma/kalshi_fast_z_exit_2026_10_14 and the local experiment directory.']
 (P/'REPORT.txt').write_text('\n'.join(lines)+'\n')
if __name__=='__main__':main()
