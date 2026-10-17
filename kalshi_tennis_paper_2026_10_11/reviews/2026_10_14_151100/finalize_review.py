"""Finalize this receipt review only; preserve frozen runtime and paper ledger."""
from pathlib import Path
from decimal import Decimal
import datetime as dt
import hashlib
import json

P = Path(__file__).resolve().parent
ROOT = P.parent.parent
assert ROOT == Path('/home/varma/kalshi_tennis_paper_2026_10_11')
a = json.loads((P/'audit.json').read_text())
s = json.loads((P/'supplemental_audit.json').read_text())
o = json.loads((P/'operations_analysis.json').read_text())
c = json.loads((P/'capture.json').read_text())
g = json.loads((P/'schedule_gap_review.json').read_text())
prior = json.loads((P/'prior_review_state.json').read_text())
manifest = json.loads((ROOT/'deployment_manifest.json').read_text())
assert all(hashlib.sha256((ROOT/k).read_bytes()).hexdigest()==v for k,v in manifest.items())
assert json.loads((ROOT/'review_state.json').read_text()) == prior
assert a['issues'] == g['explained_issues'] and not g['unresolved_issues']
assert not a['later_selected_point_corrections'] and not s['eligible_selected_keys_in_removal_or_replacement_set']
assert prior['preliminary_20_trade_reported'] and not a['milestones']['main_14day_100_reached']
assert c['captured_utc'] < a['milestones']['entry_cutoff_utc']
assert len(a['position_checks']) == 56 and len(a['eligible_decisions']) == 117
assert len(s['eligible_scenario_ownership_checks']) == 234

lines = ['TENNIS90 PAPER OPERATIONS REVIEW',c['captured_utc'],'',o['disposition'],'',
         'Scenario | attempts/matches | filled/settled | W/L | net after fees/friction | fees | friction | realized drawdown']
for scenario in ('base','stress'):
    p=a['portfolios'][scenario];e=o['execution'][scenario]
    lines.append(f"{scenario} | {p['attempted']}/{e['attempted_matches']} | {p['filled']}/{p['settled']} | {p['wins']}/{p['losses']} | ${p['net']:+.4f} | ${p['fees']:.4f} | ${p['friction']:.4f} | ${p['drawdown']:.4f}")
    for series,v in p['by_series'].items():
        lines.append(f"  {series}: {v['settled']} settled, {v['wins']}W/{v['losses']}L, ${v['net']:+.4f}.")
    new=[x for x in s['new_settlements'] if x['scenario']==scenario]
    net=sum((Decimal(str(x['net'])) for x in new),Decimal(0))
    lines.append(f"  New settlements: {len(new)}, {sum(x['payout']==1 for x in new)}W/{sum(x['payout']==0 for x in new)}L, ${net:+.4f}. Open/pending: {p['open']}/{p['pending']}.")
lines += ['','EXECUTION',
 'All117 qualified decisions,234 scenario execution/ownership paths and56 paper fills/settlements independently verified. Every eligible signal has an attempt or documented ownership exclusion. Original signal inputs, two later books, cap, active market, execution deadlines, cache/HTTP limits, worst signal/book price, fees/friction and authoritative outcomes reconcile. No qualified signal silently dropped in the recorded evidence.',
 'Normal32 attempts across30 matches produced30 fills;2 cap failures. Stress37 attempts across30 matches produced26 fills;11 cap failures. New failures: normal Krueger/Tar later filled on a new eligible minute; delayed Ostapenko/Pegula did not fill, while normal won+$0.4525. The delayed scenario cannot be credited with that missed winner. No execution request failure or missed execution deadline.',
 'Normal decision-to-fill median4.358s/max4.808s; stress61.358s/max62.177s. Minimum passing depth normal91.25/stress9.2 contracts for a one-contract entry. These are depth-supported simulated fills, not actual exchange acceptance or fill guarantees.',
 f"Same-decision settled pairs: {json.dumps(o['pair_summary']['same_decision'])}. Later-decision pairs: {json.dumps(o['pair_summary']['different_decision'])}.",
 'Four normal-only settled winners total+$2.0342. Scenario balances are independent and not additive; unequal and later-attempt samples are not a causal estimate of latency.',
 '', 'FEED AND SCHEDULE INVESTIGATION',
 'The first audit flagged a579.252s Krueger/Tar score gap from10:58:21 to11:08:00UTC. Receipt investigation explains it: advertised start changed from11:00 to11:10UTC, so the frozen pre-start polling rule paused until two minutes before the new start. No points had been received before that pause. Twelve successful post-resume snapshots remained without plays; six plays then appeared at11:10:03UTC. Possible observations during the pause remain unknown, not assumed absent.',
 'Two retrospectively supplied point timestamps (11:07:25/11:07:46) predate resumed continuous polling. They remain in all original records and original latency statistics, but are separately excluded from the supplemental conservative latency metric. A third point timestamped11:08:22 was first observed101.257s later despite continuous polling; it remains counted as a delayed observation. Provider timestamps are not independently validated.',
 'Corrected supplemental continuous-poll sample:8,201 observations,median10.241s/p9517.597s/max101.257s;1 above90s,none above120s. Original pre-refinement sample:8,203,median10.243s/p9517.606s/max158.257s. Original status/ledger values are not rewritten. No score arrival is backdated into a decision and no PnL is assigned to unobserved minutes.',
 'Opening11:10 decision had no validated prior yet because the first point payload arrived after the decision;11:11 rejected for edge,11:12 latest-state-not-point,11:13 edge. No early qualified order was omitted in the available records. Both later Krueger/Tar fills passed the original prospective-prior and score-age checks. No observer change is warranted by this isolated scheduled pause.',
 f"Total point versions:{a['feed']['point_versions']}; revised:{a['feed']['revised_versions']}; detected key-removal/replacement episodes:{len(s['point_key_removals'])}. No eligible selected point subsequently revised or included in a replacement set. No new empty HTTP200 episodes or candle-publication gaps.",
 'Eleven new HTTP failures:9 candle429 responses,1 disconnected score request,1 catalogue timeout. All recovered; candle retries completed within7.596s of the original boundary, inside20s; score recovery4.118s/catalogue60.298s. No execution request affected. Original failures retained.',
 'Prospective prior coverage48/56 matches with observed points. Newly excluded Paul/Berrettini had four earlier prior captures, then a final backward schedule move arrived114.757s after the new five-minute capture deadline. Frozen prior exclusion retained; no retrospective recapture or hypothetical qualified trade.',
 '', 'HEALTH AND REVIEW GATES',
 f"Service active/running PID4015560, NRestarts0; NTP synchronized. Snapshot heartbeat{a['health']['heartbeat_age_seconds']:.3f}s/catalogue age{a['health']['catalogue_age_seconds']:.3f}s. Dashboard HTTP{o['dashboard_http_status']}; frozen deployment hashes verified before and after review.",
 f"Calendar frequency: {json.dumps(s['calendar_frequency'])}",
 f"Elapsed{a['health']['elapsed_hours']:.2f}hours;30 normal settlements. First20 preliminary report already delivered. Both scenarios remain negative; no profitable edge established. Main review requires14 calendar days AND100 settlements and day/block uncertainty; small dependent samples cannot support a firm conclusion. Entry cutoff{a['milestones']['entry_cutoff_utc']}, then finish holdings. No automatic live promotion.",
 '', 'REVIEW CHANGE RECORD',
 'Only review scripts, derived artifacts and review_state updated. Added receipt-backed schedule-gap explanation and conservative continuity-based latency breakout. Operations audit now accepts only that exact explained issue; original audit flag, original metrics and all raw records retained. No observer/config/model/strategy/limit/service/ledger or existing-bot changes; no orders/restarts.',
 'Routine consistent review snapshot removed after successful audit; original server receipt database and first20 milestone snapshot retained. No repeat milestone notification.']
(P/'REPORT.txt').write_text('\n'.join(lines)+'\n')
(P/'REVIEW_CHANGES.txt').write_text('Added schedule_gap_review.py/json; amended review-only operations_analysis.py to require exact receipt-backed issue resolution. Added a conservative post-gap latency breakout while preserving original metrics. No frozen or runtime files changed.\n')
now=dt.datetime.now(dt.timezone.utc).isoformat()
state=dict(prior)
state.update(last_checked_utc=c['captured_utc'],last_artifact=f'reviews/{P.name}/audit.json',
 last_report=f'reviews/{P.name}/REPORT.txt',last_base_settled=30,last_stress_settled=26,
 last_operational_findings=[
  'Normal30 settled/12W18L/-1.8638;stress26/7W19L/-5.1116. No open/pending.',
  'All56 fills/settlements,117 eligible decisions/234 ownership paths verified; normal2/stress11 cap failures retained.',
  '11 new HTTP failures all recovered before applicable deadlines; no failed execution request.',
  '579s scheduled pause preceded first received scores;12 empty post-resume polls then opening batch. No known qualified miss; unobserved interval remains unknown.',
  'Supplemental conservative latency8201 observations median10.241s/p9517.597s/max101.257s;two pre-resume timestamps excluded only from supplemental metric,original records retained.',
  'Prior coverage48/56;new PAUBER exclusion is late backward schedule change. No runtime/live-bot/strategy change; first20 already reported,main gate unmet.'
 ],last_disposition=o['disposition'],checks_completed=prior['checks_completed']+1,
 last_http_error_receipt=max(x['id'] for x in s['new_http_failures']),review_completed_utc=now,
 last_schedule_gap_review=f'reviews/{P.name}/schedule_gap_review.json')
text=json.dumps(state,indent=2)+'\n';(P/'review_state.json').write_text(text)
tmp=ROOT/'review_state.json.tmp';tmp.write_text(text);tmp.replace(ROOT/'review_state.json')
snapshot=P/'paper_snapshot.sqlite'
assert hashlib.sha256(snapshot.read_bytes()).hexdigest()==c['snapshot_sha256']
snapshot.unlink()
(ROOT/'latest_review.json').write_text(json.dumps({'directory':str(P),'captured_utc':c['captured_utc'],'status':'complete','decision':'DONT_NOTIFY'},indent=2)+'\n')
verification={'completed_utc':now,'frozen_hashes_match':True,'snapshot_retained':False,
 'files':{f:hashlib.sha256((P/f).read_bytes()).hexdigest() for f in ['audit.json','supplemental_audit.json','operations_analysis.json','schedule_gap_review.json','TRADE_LEDGER.txt','REPORT.txt','review_state.json']},'decision':'DONT_NOTIFY'}
(P/'final_verification.json').write_text(json.dumps(verification,indent=2)+'\n')
print(json.dumps(verification,indent=2))
