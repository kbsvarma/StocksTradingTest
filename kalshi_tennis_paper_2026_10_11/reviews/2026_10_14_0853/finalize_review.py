"""Save review artifacts and cursor only; never write the paper ledger or runtime."""
from pathlib import Path
import datetime as dt
import hashlib
import json
import subprocess

P=Path(__file__).resolve().parent
ROOT=P.parent.parent
assert ROOT==Path('/home/varma/kalshi_tennis_paper_2026_10_11')
a=json.loads((P/'audit.json').read_text())
s=json.loads((P/'supplemental_audit.json').read_text())
o=json.loads((P/'operations_analysis.json').read_text())
c=json.loads((P/'capture.json').read_text())
prior=json.loads((P/'prior_review_state.json').read_text())
manifest=json.loads((ROOT/'deployment_manifest.json').read_text())
assert all(hashlib.sha256((ROOT/k).read_bytes()).hexdigest()==v for k,v in manifest.items())
assert json.loads((ROOT/'review_state.json').read_text())==prior, 'Concurrent review; do not overwrite'
lines=['TENNIS90 PAPER OPERATIONS REVIEW',c['captured_utc'],'',o['disposition'],'',
       'Scenario | attempts / matches | fills | settled W/L | net after fees/friction | fees | friction | realized drawdown']
for scenario in ['base','stress']:
    p=a['portfolios'][scenario]; e=o['execution'][scenario]
    lines.append(f"{scenario} | {p['attempted']} / {e['attempted_matches']} | {p['filled']} | {p['settled']} {p['wins']}W/{p['losses']}L | ${p['net']:+.4f} | ${p['fees']:.4f} | ${p['friction']:.4f} | ${p['drawdown']:.4f}")
    for series,values in p['by_series'].items():
        lines.append(f"  {series}: {values['settled']} settled, {values['wins']}W/{values['losses']}L, ${values['net']:+.4f}.")
lines += ['',
 'One open Volynets YES contract in each independent scenario, entry52c; cost54.75c normal/55.75c delayed. No pending attempts at capture. Open outcomes excluded from realized PnL. Scenario portfolios are not additive.',
 'Five additional settlements per scenario: normal1W/4L, net-$1.3459; delayed1W/4L, net-$1.4466. Full ledger includes all losses and both open positions. These additional paper outcomes do not establish an edge or a stable daily rate.',
 '', 'EXECUTION',
 '103 eligible decisions and206 execution/ownership paths reproduced. Every eligible signal had an attempt or a documented owned/pending exclusion. All51 simulated fills pass original received-input replay, two subsequent books, original cap, market-active and HTTP/cache/deadline checks, worst-of-signal-and-book price, fee and friction verification. All49 settlements reconcile to authoritative receipts.',
 'Normal1 unfilled attempt (previously reported); delayed10, including3 new cap failures in Kartal/Bencic and Muchova/McCartney. Both matches later filled on independently eligible decisions. No known qualified signal was silently dropped. No execution request failure or missed execution deadline.',
 'Normal decision-to-fill median4.357s/max4.808s; delayed61.354s/max62.177s. Minimum passing depth normal91.25 contracts/delayed9.2. These are simulated depth-supported fills; actual acceptance, cancellations and execution are unproven.',
 '17 settled same-decision pairs: normal-$2.4048, delayed-$2.6164. Six later-decision pairs: normal-$0.4204, delayed-$1.3197. Three normal-only settled winners total+$1.5817. One same-decision pair remains open. Aggregate PnL difference is not a clean causal estimate of latency.',
 '', 'FEED AND OPERATIONS',
 'Service active/running PID4015560, NRestarts0; NTP synchronized; snapshot heartbeat3.226s. Dashboard rootHTTP200. Frozen deployment hashes match before and after audit. No repair or restart required.',
 'Clean source first-arrival latency after excluding revisions, censoring and key replacements:7224 observations, median10.196s/p9517.528s/max32.120s, none above90s. This includes polling/network time and cannot independently validate the provider timestamp.',
 '7561 point versions,258 revised versions,40 removal/replacement episodes overall. No eligible selected point was later revised or in a detected replacement set. Maximum ordinary observed polling gap15.015s; no unexplained gap>30s. No new empty HTTP200 score episodes or missing-candle publication failures.',
 '23 new HTTP errors:22 rate limits and1 disconnected catalogue request. All recovered.17 candle recoveries completed within7.786s of their original boundary, within20s; score retry4.050s; catalogue maximum180.972s. No failed execution-book requests. Original error receipts retained.',
 'Prospective prior coverage44/51 matches with observed points. Two newly excluded matches, JOVYAS and PUTSHN, had repeated schedule changes clearing earlier prior captures. Final backwards schedule moves arrived160.009s and187.210s after the new five-minute fallback deadlines. This is the existing frozen schedule/prior rule; no stale prior substituted or missed qualified trade claimed. Exact receipt evidence is in operations_analysis.json.',
 '', 'FREQUENCY AND REVIEW GATES',
 'Normal entries by ET date October11=1,October12=10,October13=12,October14=4; delayed1,8,11,4. First/current dates partial.6.75 normal entries per touched calendar date versus10.233 per elapsed24h; two complete dates so far. These are descriptive, not an established daily frequency.',
 'Elapsed63.32hours;26 normal settlements. First20 preliminary report already delivered; do not repeat. Both scenarios currently negative and sample remains insufficient. Main review requires14 calendar days AND100 normal settlements, with day/block uncertainty; no reliable edge inference from this small dependent sample. Entry cutoffNovember8 17:34:37UTC, then finish holdings. No automatic live promotion.',
 '', 'CHANGE RECORD',
 'Only review scripts/artifacts and review_state.json updated. Frozen observer/model/config/ledger and existing bots unchanged; no financial orders. A consistent temporary read-only SQLite backup supported this audit. It is removed after successful verification to avoid repeated full database copies; original receipt database and the first20 milestone snapshot remain preserved. Capture hash and full derived audits retained.']
assert len(s['point_key_removals'])==40
assert len(a['position_checks'])==51
assert len(a['eligible_decisions'])==103
assert len(s['eligible_scenario_ownership_checks'])==206
(P/'REPORT.txt').write_text('\n'.join(lines)+'\n')
now=dt.datetime.now(dt.timezone.utc).isoformat()
state=dict(prior)
state.update(last_checked_utc=c['captured_utc'],last_artifact='reviews/'+P.name+'/audit.json',
    last_report='reviews/'+P.name+'/REPORT.txt',last_base_settled=26,last_stress_settled=23,
    last_operational_findings=[
      '26 normal settled/11W15L/-1.2435;23 delayed/7W16L/-3.9361. One open each; no pending.',
      'All51 paper fills/49 settlements,103 eligible decisions/206 ownership paths verified. No unexplained missed qualifying signal.',
      '23 HTTP errors recovered,17 candle retries before7.786s of boundary. No new empty scores, candle gaps or unexplained >30s score gaps.',
      'Prior coverage44/51;two new final backward schedule changes arrived after fallback deadlines. Existing frozen exclusion; no hypothetical trades.',
      'Clean latency7224 median10.196s/p9517.528s;no selected corrections. Active/NRestarts0,NTP/dashboard/frozen hashes healthy.',
      'First20 report already delivered;no new milestone. Both scenarios negative,insufficient evidence. No runtime/live-bot/strategy changes.'
    ],last_disposition=o['disposition'],checks_completed=prior['checks_completed']+1,
    last_http_error_receipt=max(x['id'] for x in s['new_http_failures']),review_completed_utc=now)
(P/'review_state.json').write_text(json.dumps(state,indent=2)+'\n')
tmp=ROOT/'review_state.json.tmp'
tmp.write_text(json.dumps(state,indent=2)+'\n')
tmp.replace(ROOT/'review_state.json')
snapshot=P/'paper_snapshot.sqlite'
assert hashlib.sha256(snapshot.read_bytes()).hexdigest()==c['snapshot_sha256']
snapshot.unlink()
verification={'completed_utc':now,'frozen_hashes_match':True,'snapshot_retained':False,
              'review_state_sha256':hashlib.sha256((ROOT/'review_state.json').read_bytes()).hexdigest(),
              'report_sha256':hashlib.sha256((P/'REPORT.txt').read_bytes()).hexdigest(),
              'decision':'DONT_NOTIFY'}
(P/'final_verification.json').write_text(json.dumps(verification,indent=2)+'\n')
print(json.dumps(verification,indent=2))
