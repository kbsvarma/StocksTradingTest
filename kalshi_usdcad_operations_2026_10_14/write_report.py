from pathlib import Path
import json,datetime,hashlib
from zoneinfo import ZoneInfo
P=Path(__file__).resolve().parent
r=json.loads((P/'passive_audit.json').read_text());h=json.loads((P/'quote_health_final.json').read_text());cad=r['cad'];lim=r['rate_limits']
end=datetime.datetime.fromtimestamp(r['ts'],ZoneInfo('America/New_York')).isoformat()
started=h['requests'][0]['started'];elapsed=r['ts']-started
text=f'''USD/CAD operational audit and dashboard quote repair
Checked through {end}

RESULT
The delayed display was a real refresh failure, not merely wording. The old dashboard issued up to eight public listing requests in a burst every ten seconds and backed off each failed asset for 20, 40, then 60 seconds. Any failed request marked its retained old price "Quote delayed", even if that price was only 29 seconds old. That label accurately disclosed stale/cache uncertainty but did not explain the request failure.

The deployment changes ONLY dashboard_quotes.py and dashboard_panel.py. The trader, execution observer and tennis processes kept their PIDs; weather remains canceled/inactive. All other production Python/CSS hashes and ARM/HALT flags match the initial manifest. Original BTC z3, all USD/CAD/other lane rules, five-contract sizing, both price caps, shared25/$25/$20 and retry policies are unchanged. No funds moved or manual financial orders submitted. The frozen September28 checkpoint was not touched.

DISPLAY REPAIR NOW LIVE
- One GET /markets?tickers=... refreshes all discovered current/upcoming markets together, replacing eight per-asset price refreshes. Steady price refresh traffic falls 87.5%; paced listing discovery is additional and usually occurs around market rollover.
- A single shared background worker serves every viewer, uses persistent connections, spaces requests at least1.25s apart, and makes no new display request during seconds0–7 of each minute, leaving the trader's usual initial scan clear.
- Discovery uses actual listings, not invented market times. Missing current listings trigger rediscovery. Current/upcoming rollover, closed sessions, and invalid/missing quote behavior are retained.
- Global failure backoff starts at2seconds and caps at30seconds. It does not launch eight independent retry bursts. Old prices retain their original timestamps. The UI now names HTTP429 rate limiting or other failures and shows the scheduled next refresh.
- Collection pauses after45seconds without an active viewer. Local read-only diagnostic output is state/dashboard_quote_status.json; it contains public quotes/request timing, not credentials. No extra monitoring service or automation was created.

VERIFICATION
23 tests passed locally and on server, covering old normalization/window/price separation behavior, batched exact-ticker GETs, request pacing, shared viewers, repeated failures/backoff, retained ages, missing listings/quotes, idle/resume and visible errors. One controlled public batch GET confirmed real EUR/USD, WTI and USD/CAD payloads before installation.
After deployment, {len(h['requests'])} requests over {elapsed:.1f}seconds: {sum(x['kind']=='batch' for x in h['requests'])} batches, {sum(x['kind']=='discovery' for x in h['requests'])} discovery requests, {sum(bool(x['error']) for x in h['requests'])} HTTP/request failures. All eight current asset errors are empty. At the final receipt read quotes were {max(r['ts']-v['checked_ts'] for v in h['states'].values()):.1f}s old. Actual browser verified prices changing and all three affected assets showing "Public quote · fetched 0m01s ago"; screenshot dashboard_verified.jpg. This short observation is not a guarantee against future exchange throttling.

USD/CAD LIVE AUDIT
From activation13:28ET through the cutoff: {cad['decision_minutes']} completed decision minutes, {cad['scan_events']} saved scan events, {cad['qualified_signals']} qualified signals, {cad['executable_events']} executable events, {len(cad['orders'])} submitted market records. There is no observed qualified CAD trade missed in this sample, but no positive execution/fill validation yet either.
Status counts: {json.dumps(cad['status_counts'],sort_keys=True)}. Events are repeated lane checks, not independent markets/trades.
Original scan completion after minute start: median{cad['first_scan_latency']['cushion_z2']['median']:.3f}s, maximum{cad['first_scan_latency']['cushion_z2']['max']:.3f}s. Premium median{cad['first_scan_latency']['premium_z3']['median']:.3f}s, maximum{cad['first_scan_latency']['premium_z3']['max']:.3f}s. Healthy early scans showed CAD roughly0.43s behind BTC; it is last in the sequential asset scan, which becomes a bottleneck if earlier assets stall.
At13:36 the captured complete source had61 completed minute bars plus opening reference. CAD z0.9766 at9minutes failed original required2.1740; it was not an executable/qualified missed trade even though a price/book snapshot existed. Underlying references and prices must satisfy the frozen curve, spread, window and live cap/depth checks.
NTP synchronized={r['ntp']}; live heartbeat age{r['heartbeat_age']:.2f}s, no halt or runtime error. CAD enabled, route0, funded; latest displayed FX cash$112.31. Whole-cent tick ladder means effective maximum93c original and97c premium under existing93.9/97.5c caps; 94c/98c offers cannot be bought by simply reading the headline caps.

TRADING API ERRORS: RECOVERY, NOT PROOF OF NO LOST PRICES
The rolling24h ledger contains {lim['events']} HTTP429 data-error events in {lim['affected_lane_minutes']} lane-minutes: {json.dumps(lim['by_asset'],sort_keys=True)}. All{lim['recovered']} recovered to a substantive scan in the same minute; worst recovery completion was{lim['recovery_max_seconds']:.3f}s after minute start. No unresolved lane-minute in that window. Five recoveries produced qualified signals: four markets eventually filled five contracts; WTI03:15ET did not, with subsequent books failing price/depth. One of the four fills, WTI11:15ET, filled on a later decision rather than the first recovering decision. Full rows in recovered_qualified_outcomes.json.
Missing simultaneous pre-error executable books prevent claiming that no better price was lost during those delays. This audit does not turn error counts into hypothetical extra trades/PnL.

AUDIT INTERFERENCE DISCLOSURE
The initial finite USD/CAD timing probe added public traffic and overlapped four trading429 events; it was stopped immediately when this appeared. There were{lim['before_probe']}429 events before the probe, {lim['during_probe']} during its interval, and{lim['after_probe']} afterward in the audit window. The probe itself made49public GETs,47HTTP200/2HTTP429. Its latency observations are not an uncontaminated natural baseline. Original raw receipts/samples and stop reason are preserved; it is no longer running. All further investigation used passive production receipts except the one pre-install batch validation GET. There are no load-test processes left running.

OTHER EXECUTION WEAKNESSES FOUND, NOT CHANGED BY THIS UI REPAIR
1. Slow account/final-book GET retries can outlast the execution window. An offline reproduction against the actual client, with every HTTP call and sleep mocked, consumed30.5 simulated seconds for four5second timeouts plus1.5/3/6second sleeps. FreshAccountReads waits for all futures without an enclosing signal deadline, and pagination can add further requests. Existing freshness guards stop stale submission; they cannot recover the spent window. This is a reproduced failure-mode risk, not a measured30.5second CAD stall today. A separate bounded account-read deadline is the next operational improvement; it must retain full fresh risk checks and ambiguous-order protections.
2. CAD inherits the current same-minute zero-fill retry policy: up to3IOCs within25seconds after authoritative canceled-zero proof. Only WTI currently permits a newly qualified later-minute retry. The offline case proves a confirmed zero CAD order locks later minutes of that market. No CAD order/zero fill exists in this live sample, so no lost CAD trade has yet been demonstrated. Extending the existing WTI guarded mechanism is preventive work and should not be presented as a measured frequency gain.
3. Sequential scanning and Original head-start can delay Premium under slow sources or repeated Original book checks. Current observed CAD timing is fast; stress-case latency remains. Dashboard batching removes avoidable display load but does not resolve every possible trading-path delay.

RECORDS / ROLLBACK
Original UI files in original/, installed candidate in candidate/, deployment hashes/service identities in ui_deployment.json and preservation_before/after.json. First install was automatically rolled back because the health check incorrectly used127.0.0.1 although Streamlit binds192.168.3.136. Correcting the checker and redeploying succeeded. No trader/observer/tennis restart occurred in either attempt. The prior-start receipt is retained. Restore the two original UI files and restart only kalshi-dashboard.service if needed; do not rerun the CAD activation script.
Full read-only ledger excerpts: events_final.json, rate_limit_recovery.json, recovered_qualified_outcomes.json, passive_audit.json. No production ledger writes by these audit scripts.

PRIMARY API REFERENCES
https://docs.kalshi.com/api-reference/market/get-markets documents comma-separated tickers.
https://docs.kalshi.com/getting_started/rate_limits documents429/backoff. Its authenticated tier limits do not establish the exact anonymous IP budget used by these public GETs.
'''
(P/'REPORT.txt').write_text(text)
(P/'test_receipt.json').write_text(json.dumps(dict(local_tests=23,server_tests=23,passed=True,network_mocked=True,candidate_sha256={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in (P/'candidate').glob('*.py')}),indent=2))
print(json.dumps(dict(end=end,scan_minutes=cad['decision_minutes'],qualified=cad['qualified_signals'],quote_requests=len(h['requests']),quote_seconds=elapsed,quote_errors=sum(bool(x['error']) for x in h['requests']))))
