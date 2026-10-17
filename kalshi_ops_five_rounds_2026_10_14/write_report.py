from pathlib import Path
import json,datetime
from zoneinfo import ZoneInfo
P=Path(__file__).resolve().parent
r=json.loads((P/'evidence/runtime_review.json').read_text());pres=json.loads((P/'evidence/final_preservation.json').read_text())
cpu=[json.loads(x) for x in (P/'cpu_results.jsonl').read_text().splitlines()];bench=[json.loads(x) for x in (P/'benchmark_results.jsonl').read_text().splitlines()]
ET=ZoneInfo('America/New_York')
def stamp(t):return datetime.datetime.fromtimestamp(t,ET).strftime('%Y-%m-%d %H:%M:%S ET')
def metric(d):return f"n={d['n']}, median={d['median']:.3f}s, p95={d['p95']:.3f}s, max={d['max']:.3f}s" if d['n'] else 'No observations yet'
a=r['after'];b=r['before'];initial=r['initial_five_minute_check']['after']
text=f'''FIVE-ROUND LIVE OPERATIONS REVIEW — {stamp(r['ts'])}

Outcome: operational fixes deployed, strategy and risk configuration unchanged.
Eight assets x two strategy lanes. Five production source files changed: engine.py, runner.py, execution_retry.py, market_data.py and kalshi_client.py. Deployed in four guarded stages during quiet periods, with the final boundary guard installed at {stamp(r['final_deployment_ts'])}. Only the trading service restarted. All 456 pre-existing market records and 461 attempts remain identical, all activation/halt flags match, all other production sources match, and all 12 stable checkpoint files remain unchanged.

1. DATA ARRIVAL AND FIRST SCAN
The old runner intentionally waited until two seconds after the minute and then slept in one-second increments, adding up to another second of scheduler delay. It now starts checking at +0.25s and wakes at that boundary. It continues to use completed minute candles and all 61 required completed underlying prices, freezing only valid complete inputs; missing candles and sources are refetched within existing limits. No intraminute trading, forward filling, provisional-current-minute data or lower z threshold was added. Public GET results arriving after their time budget are discarded. Server NTP: {r['ntp']}.
Checks remain at least one second apart per lane/asset, at most 24 checks, within the existing 25-second retry scheduling deadline. First submissions retain the existing 35-second final freshness guard. The final verification also caught and fixed a status-file write crossing +0.25s and causing an additional one-second sleep. Unstarted ready scans now wake within10ms rather than sleeping another second; blocked premium scans do not busy-loop. Starting earlier can capture an earlier published version of the same completed-minute input; it is not a new mathematical signal or a guarantee of identical individual selections.

2. SIGNAL PROCESSING AND SCHEDULING
Every lane formerly decoded all 456 historical order records repeatedly for ownership and retry decisions. The new SQL projection returns all owned tickers but decodes full records only for unfilled candidates. Pending state uses a fresh SQL existence check, and each risk check reads its ledger once. There is no stale ownership cache; external changes are visible on the next query.
Actual CPU benchmark on the copied 456-record ledger, 20 iterations of all 16 lane ownership/retry lookups: original median {cpu[0]['entire_16_lane_ownership_retry_lookup_ms']['median']:.2f}ms; new median {cpu[1]['entire_16_lane_ownership_retry_lookup_ms']['median']:.2f}ms. This is a local code benchmark, not exchange latency.
Routine account/display/settlement work yields while entry checks remain pending. Noncritical maintenance also stops starting after second52 so it cannot routinely cross the next minute boundary. Pending/ambiguous orders and reconciliation faults still take precedence and block submissions until resolved. Original-before-premium ordering and the eight-check original head start remain unchanged.

3. ORDER PATH AND UNNECESSARY READS
The prior preliminary book check allowed Original books above the actual execution cap, and did not consistently apply market tick rounding. It then fetched fresh positions/orders/balance and the final book just to reject an already-unexecutable price. Preliminary checks now use the same pure limit-price/tick-ladder calculation as the engine. The final fresh account and final fresh book checks remain mandatory when the preliminary book qualifies. The entry floor, spread, price caps and depth requirements have not been loosened.
Recorded-book replay of all 201 available execution-check events in the captured prior24h: old preliminary path passed201; repaired path passes55 and immediately book-blocks146 (72.6%). These146 checks span17markets/18market-lane-minute signals; the recorded final outcomes were145 insufficient-depth-inside-cap and1 reconciliation-pending. These are repeated checks, not146 missed trades. This replay demonstrates avoidable account batches on those saved books; it does not prove an identical future fill sequence. A book can change between polls in either direction, and the retained final fresh book remains authoritative.
Fully reconciled terminal fills are no longer fetched again every10seconds while awaiting expiry. Their full open risk remains reserved, actual positions are freshly checked before new orders, and the order and fills are fetched again when settlement checking begins. Legacy open rows without proof and all unresolved/partial-accounting states still require reconciliation. This removes up to10 redundant order/fill GETs per routine poll for five known open positions.

4. RETRIES, FILLS AND RECONCILIATION
Transient GET timeouts, HTTP429 and server errors before a new durable intent now retain the valid frozen signal for bounded same-minute retry. Every retry repeats the fresh account and final book checks. Permanent errors, invalid data, cash/risk failures and any ambiguous submission remain fail-closed. HTTP POST/DELETE still have exactly one transport attempt; this is not an automatic financial-write retry.
The guarded fresh-later-minute zero-fill retry now applies to all eight active assets, on both lanes. The same ticker, lane, side, route and expiry must requalify from NEW completed-minute data. ALL previous exchange orders/fills must confirm terminal cancellation, zero filled, zero remaining and zero charged cost/fees. Maximum THREE TOTAL IOC submissions across minutes and restarts remains unchanged. No top-ups after any fill and no retries after ambiguous submissions. Copper remains retired and AUD/USD deferred.
Additional fields now record scan start/completion, signal qualification, execution/account/book validation, durable-reserve duration, submit/ack response and fill confirmation. ACK is still not treated as a fill. Latency uses local receipt timestamps, not an unmeasured exchange matching-engine timestamp.

5. FAILURE TESTS AND END-TO-END VERIFICATION
280 offline unittest cases passed in the final deployment suite. New coverage includes 16 asset/lane subcases for fresh zero-fill retries through the three-attempt limit, transient read recovery, disappearing cash, ambiguous POST handling, expiry and quantity/price/risk guards, pending fill indexing, old open rows without proof, settlement rechecks, fresh SQL ownership, wakeup/check pacing and preliminary-vs-final tick caps. Historical regression fixtures and all failed intermediate logs are retained. Initial failures were missing copied evidence and a mocked runner fixture that assumed account failures preceded every scan; the fixture now asserts that scans continue while no order can bypass the failed fresh account read.

CONTROLLED SIMULATION — minute boundary to simulated POST dispatch
Identical mocked transport costs and inputs; no network and no real order:
'''
for scenario in ('normal','slow_other_route','open_positions'):
 old=next(x for x in bench if x['version']=='original' and x['scenario']==scenario);new=next(x for x in bench if x['version']=='candidate' and x['scenario']==scenario)
 text+=f"{scenario}: {old['submit_seconds']:.2f}s -> {new['submit_seconds']:.2f}s.\n"
text+=f'''
These are decision-minute-boundary timings, not elapsed time from an already-qualified signal. They separate algorithmic scheduling improvements from changing network conditions.

FIRST FIVE-MINUTE LIVE CHECK (before the last wake-up correction): {metric(initial['active_window_all'])};80lane observations, no errors. This detected the isolated extra-second wakeup that was subsequently repaired.

LIVE OBSERVATION — minute boundary to first substantive scan completion
Before: {b['decision_minutes']} completed decision minutes after the preceding14:01 release and before this task's first deployment. After: {a['decision_minutes']} completed decision minutes after the final deployment, through {stamp(r['ts'])}. In-progress minutes and deployment partial minutes excluded. Entry-window observations exclude outside_window and temporary source/candle absence; error and missing-input counts are retained separately.
All entry-window lanes before: {metric(b['active_window_all'])}
All entry-window lanes after:  {metric(a['active_window_all'])}
First scan STARTS after: {metric(a['first_scan_started'])}
'''
for asset in r['status']['fresh_retry_assets']:
 text+=f"{asset}, both lanes before: {metric(b['active_by_asset'][asset])}; after: {metric(a['active_by_asset'][asset])}\n"
text+=f'''
After-window scan statuses: {json.dumps(a['status_counts'],sort_keys=True)}.
After-window data errors: {len(a['errors'])}; qualified market-lane-minute signals: {a['qualified']}; executable market-lane-minute signals: {a['executable']}; new actual market attempts after final deployment: {len(r['new_orders'])}.
Live samples are short, sequential windows with different market conditions, not a randomized latency trial or proof of more profitable fills. Source receipt availability, network outages and exchange liquidity remain external constraints. No guaranteed fill or quantified incremental P&L is claimed.

Runtime: LIVE/armed, no error/halt/stopped flag; heartbeat age{r['heartbeat_age']:.2f}s; dashboard health {r['ui_health']}; public quote errors {json.dumps(r['quote_errors'])}. Trading PID {r['services']['kalshi-crypto-z2-live.service']['MainPID']}; dashboard, execution observer and tennis PIDs unchanged. Weather remains canceled/inactive.

UNCHANGED: all eight asset profiles and z/time gates; original BTC z3; USD/CAD adaptive original1.75/premium2.75 at5min; five contracts; original93.9c and premium97.5c buy caps with exact tick rounding; shared25contracts/$25exposure/$20daily loss; premium sublimits; full-size displayed depth; one market ownership; no partial-fill top-up; durable intent before POST. No manual orders, funding transfers or other-bot edits.

Artifacts: candidate/ and original/; original_hashes.json; four deployment receipts and preserved-row JSON files; evidence/runtime_review.json and comparison_events.json; evidence/final_preservation.json; cpu_results.jsonl; benchmark_results.jsonl; book_gate_original.json and book_gate_candidate.json; phase4_final_tests.log. Server backups include SQLite snapshots; large backups stay on the server. Do not rerun completed deployment scripts. To undo this release, restore the five original code files while safely stopped/reconciled; NEVER restore a stale ledger or old activation flags.
'''
(P/'REPORT.txt').write_text(text)
(P/'README.txt').write_text('LIVE operational repair; final deployment October14,2026. See REPORT.txt for all five rounds, live versus simulated timings, preserved settings and limitations. Do not rerun deployment scripts. Server package /home/varma/kalshi_ops_five_rounds_2026_10_14. All strategy/price/size/risk settings unchanged.\n')
print('Reports saved at',stamp(r['ts']))
