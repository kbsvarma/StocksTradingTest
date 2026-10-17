"""Create a reproducible report from the frozen audit artifacts."""
import json,gzip,collections
from pathlib import Path
from decimal import Decimal as D
P=Path(__file__).resolve().parent
def read(n):return json.loads((P/n).read_text())
s=read('summary.json');c=read('miss_classification.json');h=read('final_health.json');x=read('exchange_proof.json');supp=read('supplemental.json');ten=read('tennis_status.json');ta=read('tennis_audit.json')
assert x['all_checks_pass'] and len(x['proof'])==64 and supp['deployment_match']
assert sum(v['filled'] for v in s['by_asset'].values())==62
assert sum(D(v['net']) for v in s['by_asset'].values())==D('12.761200')
categories={}
for t,r in c.items():
 if t=='KXXRP15M-26OCT100930-30':category='IOC canceled with zero fill; later books outside cap'
 elif t=='KXETH15M-26OCT092000-00':category='Terminal below-floor rejection; later book recovered'
 elif r['valid_within_retry']:category='Affordable initial/observer snapshot; final execution book failed'
 else:category='No observed full five-contract book within effective cap and floor'
 categories[t]=category
assert collections.Counter(categories.values())=={
 'IOC canceled with zero fill; later books outside cap':1,
 'Terminal below-floor rejection; later book recovered':1,
 'Affordable initial/observer snapshot; final execution book failed':3,
 'No observed full five-contract book within effective cap and floor':13}
lines=['KALSHI LANE OPERATIONS AUDIT — 2026-10-11',
'Final health check: '+h['et'],
'Performance/event window: October 9 11:07 ET through October 11 21:21 ET, exclusive.',
'This window begins immediately after the October 9 public-data repair. All entries here requested five contracts. These are actual live results, not a backtest.',
'',
'FINDING',
'The live bot is operational, with no current halt, funding shortage, unmatched exchange state, missed scan minutes or entry-window market-discovery failure. Its remaining weaknesses are fleeting executable books, temporary public/authenticated API failures and occasional unavailable input history. No strategy, risk, funding, trading service, live database or checkpoint was modified by this audit.',
'',
'FUNNEL AND RESULTS',
'48,916 of 48,916 expected asset/lane/minute scans recorded; none missing. First scan completion median 2.96s, p95 3.63s, worst 21.64s after the minute boundary.',
'80 unique markets passed quote/time/spread/z gates before the stricter execution cap. 63 markets received 64 IOC submissions; 62 markets filled all five contracts, one remained zero-filled. No partial fills. 60 wins, 2 losses; $12.7612 net after actual fees ($1.55294).',
'All 64 submissions independently matched authenticated exchange order IDs/client IDs, quantities, costs and fees. Settled results and P&L also matched the exchange catalogue. 71 individual fill receipts aggregate to those orders. No orphan orders were found in the queried interval.',
'Do not call 62/80 an exchange fill rate: 17 qualifying markets never reached submission. Submitted-market fill rate was 62/63; submitted-order fill rate 62/64 because NATGAS first canceled zero and then filled its second IOC.',
'',
'Asset      Qualified  Filled  Wins/Losses   Net after fees   Original/Premium fills']
for a,v in s['by_asset'].items():
 z2=s['by_asset_lane'][a+'/cushion_z2']['filled'];z3=s['by_asset_lane'][a+'/premium_z3']['filled']
 lines.append(f"{a:<10} {v['qualified_markets']:>8} {v['filled']:>7}  {v['wins']:>3}/{v['losses']:<3}       ${D(v['net']):+.4f}            {z2}/{z3}")
lines+=['COPPER     Intentionally paused by HALT_COPPER; not an operational failure.',
'',
'Original lane: 51 fills, 49W/2L, +$10.9431. Strong-cushion lane: 11 fills, 11W/0L, +$1.8181. Premium qualified in 16 unowned markets and filled 11. All seven active assets have actual fills in both profiles in this window. Noncrypto thresholds differ by asset; the lane names z2/z3 are labels, not a claim of identical thresholds.',
'Original had 72 qualifying markets and premium 16, with 8 overlaps. Four original-qualified markets were eventually owned by premium; one market owner is intentional. Already-owned skips have no computed z and are not evidence of another independently qualifying signal.',
'Daily entries within this review window (Oct9 is a partial day): '+str(supp['daily_in_review']),
'Losses: WTI KXWTI15M-26OCT091845-45, -$4.4370, original z1.5636; BTC KXBTC15M-26OCT101900-00, -$4.6540, original z2.1907. Both filled/settled/accounted correctly. These two observations do not justify retuning filters.',
'',
'WHY 18 QUALIFYING MARKETS DID NOT FILL',
'13: no sampled full-five-contract book met the exact tick-rounded cap and floor. Raising the signal ceiling does not authorize paying above the execution cap. This remains a price/liquidity constraint under the current design. Sparse snapshots cannot rule out unsampled liquidity.',
'3: affordable initial/observer books changed before the engine execution book. NATGAS Oct9 21:45, XRP Oct10 20:00 and BTC Oct10 20:30. The initial-to-final book gaps were 0.223s, 0.226s and 0.253/0.262s. Account positions, resting-order and cash checks currently occur between these book reads. No full-five-contract passing final execution book was ignored.',
'1: XRP Oct10 09:30 got a real IOC at 09:24:05.615 ET after a passing 93.8c book at 09:24:05.612, limit93.9c. Exchange confirmed canceled/zero by 09:24:07.125. Subsequent sampled books did not support another order inside the cap. The 25s/lane-owned zero-fill lock was intentional, not a stuck account.',
'1: ETH Oct9 20:00 initially fell to79c, below the85c floor, which terminates that signal minute by design. The observer saw five contracts around90.6c at19:57:12, inside its91.2c effective cap, but the signal was already invalidated. Re-entering after that recovery would change the strategy guard; it is not a routine execution repair.',
'No additional passing observer books appeared only after25s in these 18 markets. All joins used exact ticker/lane/decision minute, not a nearby signal.',
'',
'Exact missing-market classification:']
for t,category in categories.items():lines.append('  '+t+': '+category)
lines+=['',
'API AND INPUT GAPS',
'76 HTTP429 public-data errors and4timeouts affected30 asset/lane/minute decisions. All30 recovered within that minute; subsequent data/price/z gates ran. The BTC23:52 event recovered to a usable scan at5.58s and that market later filled premium at23:55. No persistent DNS, authorization or global entry fault exists now.',
'Eight newly created orders briefly returned GET404, interrupting9 entry checks across assets. These appeared0.33–0.87s after order creation and cleared through reconciliation. Two affected qualifying markets ultimately missed, but their sampled books do not prove an affordable five-contract fill was lost because of the404. Do not bypass reconciliation of uncertain orders.',
'Six input failures across four unique asset/minutes exhausted the retry window: NATGAS Oct9 19:12 (both profiles), WTI Oct11 18:05 (original), NATGAS Oct11 18:05 (premium), EURUSD Oct11 18:43 (both profiles).',
'Later historical inspection: NATGAS Oct9 now has complete history and z0.8391, below both thresholds, so it does not establish a skipped qualifying trade. EURUSD still has no usable exact decision quote. The Sunday WTI/NATGAS episode is consistent with reopening warm-up: warm_sources fetches prior settled charts, which at reopening are from Friday. The current event chart is fetched only after the price gate. Historical chart windows obtained later cannot fully reconstruct those live caches.',
'The live incomplete_spot record does not preserve which bar/reference was missing, response timing or the rejected source payload. This is a real diagnostic gap. Later source revisions are not point-in-time evidence; no counterfactual profit is claimed for these gaps.',
'Catalogue cross-check found zero no_current_market records during an allowed entry time when a corresponding listed market existed. Crypto discovery delays occurred at13–15min remaining, outside its3–10min window. Weekend noncrypto closures account for most absent-market statuses.',
'',
'WHAT IS ALREADY HELPING',
'22 of62 fills arrived on the second or later book check; those trades netted+$3.0158. This documents actual use of retries, not proof all22 would be permanently lost in another implementation. NATGAS Oct9 17:00 specifically filled its second confirmed-zero IOC and netted+$0.4213.',
'From final execution book receipt to durable order intent, median latency was2.83ms, max3.06ms. The larger opportunity is before that final book: account round trips and duplicate polling, plus occasional API stalls. The exchange network and actual matching time are not measured by that2.83ms number.',
'',
'RECOMMENDED OPERATIONAL WORK, IN ORDER',
'1. Reduce the time between a passing signal and the authoritative execution book. Separate quote/z computation from book fetching, refresh/reconcile account risk before taking the final book, and eliminate redundant sequential book reads. Prefer a validated streaming book/account cache where supported, with sequence/freshness checks and fresh-GET fallback. Retain full-five depth, same cap/floor, durable intent, ownership and fail-closed ambiguous-order reconciliation. The three fleeting-book markets are specific validation cases, not promised extra fills.',
'2. Coordinate public request pacing/cache use across observers and lane scans, handle429 backoff without spending the whole decision window, and show rate-limit/timeout counters. The existing retry repair recovers errors, but repeated stalls can consume most of25s. Do not increase blind polling or remove safety checks.',
'3. Prewarm active underlying charts before reopening/entry windows and record missing timestamps/reference separately from provider errors. Alert on repeated unusable input minutes. Preserve contiguous61bar and source-receipt requirements; do not forward-fill gaps or substitute revised historical data.',
'4. Make signal-qualified, full-depth executable, submitted, filled, canceled-zero and API/data skips distinct in the UI. Preserve effective tick-rounded cap and last blocking reason. Recovered404s should remain visible in diagnostics without looking like a permanent account halt.',
'No capacity increase is supported by this audit: there were zero observed cash, shared exposure or daily-budget entry rejections in the reviewed event funnel. Re-arming a floor-invalidated signal or a zero-filled market in a later minute is a separate strategy/retry-policy experiment.',
'',
'CURRENT HEALTH AND PRESERVATION',
'LIVE/armed, no halt/error, heartbeat about1.08s old at final check. Account GET confirmed no positions and no resting orders; route2 cash$62.1370 and route0 cash$107.0756. Limits remain5 per entry, shared25 contracts/$25 exposure/$20 daily risk. Copper stays paused.',
'Trader, dashboard, execution observer and tennis paper service remained active with identical PIDs/restart counts throughout. Live UI health8507=200; tennis root8516=200. Host NTP synchronized. All22 live source hashes match the Oct9 deployment; all6 ARM/HALT hashes and12 immutable baseline files unchanged. No production code or config edits/restarts.',
'',
'TENNIS PAPER',
'Both normal and60s-stress scenarios:0attempts,0fills,0settled,0fees/friction,$0net. No point-feed delay or execution-depth samples yet, so there is no forward edge verdict.',
f"At the final paper receipt check: {ta['counts']['receipts']} HTTP receipts, zero HTTP errors, 34 future matches, zero prospective priors due. First scheduled starts23:00ET; first30min prematch capture due22:30ET. Catalogue polling maximum gapabout70s against a60s schedule; milestone refresh maximumabout354s against300s; these are not live point polling gaps. HTTP median{ten['http_duration_seconds']['p50']}s/p95{ten['http_duration_seconds']['p95']}s/max{ten['http_duration_seconds']['max']}s measures requests, not the feed's publication delay.",
'SQLite quick_check=ok, fresh heartbeat, synchronized clock, ten deployed file hashes unchanged. No corrections, live-point polls, books or executions are due yet. Strategy/config remain frozen. The20settled operational milestone and14day/100settled edge review remain unmet.',
'',
'EVIDENCE AND REPRODUCTION',
'audit.py makes a consistent SQLite backup using a read-only production connection. Server-only audit.sqlite3 is the immutable audit snapshot; do not restore it over current state.',
'summary.json, coverage.json, events.json.gz, attempts.json.gz, orders.json.gz: source ledger and funnel. summary.daily originally contains whole Oct9; use supplemental.daily_in_review for dates clipped to this report window.',
'classify.py / miss_classification.json: exact cap/floor/depth and independent observer joins for all18 misses. input_failures.json: complete input-error traces.',
'exchange_audit.py / exchange_proof.json / exchange_orders.json.gz / exchange_fills.json.gz / markets.json.gz: independent GET-only exchange verification.',
'check_input_gaps.py / input_gap_later_check.json / input_gap_later_receipts.json.gz: explicitly retrospective source inspection, not tradable replay.',
'final_check.py / final_health.json / tennis_audit.json: preserved code/checkpoint/flags, unchanged services, fresh health and paper ledger verification.',
'All audit outputs are in this directory locally and /home/varma/kalshi_lane_operations_2026_10_11 on the new server. No order-submission endpoint was called by this audit.']
(P/'REPORT.txt').write_text('\n'.join(lines)+'\n')
(P/'findings.json').write_text(json.dumps(dict(window=s['window_start_et'],through='2026-10-11T21:21:00-04:00',qualified=80,filled=62,net='12.7612',miss_categories=categories,production_changed=False,diagnostic_recommendations=['reduce pre-execution round trips','coordinate public API pacing','active-source prewarm and missing-bar receipts']),indent=2)+'\n')
print('Report written; exchange, aggregate and classification assertions passed.')
