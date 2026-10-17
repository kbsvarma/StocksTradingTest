"""Read-only audit. Pregame transport checks never establish live score latency."""
import argparse,collections,datetime as dt,json,sqlite3,time
from pathlib import Path
from core import dec,percentile,ts

def stats(values):
    return dict(n=len(values),p50=percentile(values,.5),p95=percentile(values,.95),maximum=max(values) if values else None)

def build_report(store,cfg):
    db=store.db;now=time.time();catalogue=store.meta('catalogue') or {};games=catalogue.get('games',[])
    checks=[dict(r,body=json.loads(r['data'])) for r in db.execute("SELECT * FROM checks WHERE kind='book_pair'")]
    pairs=[r for r in checks if r['body']['stable']];mismatches=[r for r in pairs if not r['body']['match']]
    rows=[dict(r) for r in db.execute('SELECT * FROM orders')];settled=[r for r in rows if r['status']=='settled']
    splits=[]
    for g in games:
        ev=g['event'];plays=[dict(r) for r in db.execute('SELECT * FROM plays WHERE event=?',(ev,))]
        clean=[p for p in plays if p['clean'] and p['received']>=g['start'] and p['wall']>=g['start']]
        lag=stats([p['lag'] for p in clean]);span=(max(p['received'] for p in clean)-min(p['received'] for p in clean))/60 if clean else 0
        score=[dict(r) for r in db.execute("SELECT received,status,error,duration FROM receipts WHERE event=? AND kind='score' ORDER BY received",(ev,))]
        gaps=[b['received']-a['received'] for a,b in zip(score,score[1:])]
        livepairs=[r for r in pairs if r['event']==ev and r['body']['game_live']]
        probes=[r['body']['elapsed'] for r in livepairs if r['body']['match'] and any(x['fill'] for x in r['body']['probes'])]
        reasons=[]
        if len(clean)<cfg['verification_min_clean_plays']:reasons.append('insufficient_live_first_arrivals')
        if span<cfg['verification_min_score_minutes']:reasons.append('insufficient_live_duration')
        if lag['p95'] is None or lag['p95']>cfg['verification_p95_source_delay_seconds']:reasons.append('score_delay_unverified_or_too_slow')
        if any(p['reason']=='future_source_timestamp' for p in plays):reasons.append('future_source_timestamp')
        if len(probes)<cfg['verification_min_game_execution_probes']:reasons.append('insufficient_live_book_probes')
        if probes and percentile(probes,.95)>cfg['verification_p95_execution_seconds']:reasons.append('execution_probe_too_slow')
        if any(not r['body']['match'] for r in livepairs):reasons.append('live_book_mismatch')
        recent=[r for r in score if r['received']>=now-60]
        if not recent or recent[-1]['error'] or now-recent[-1]['received']>cfg['maximum_poll_gap_seconds']:reasons.append('score_not_current')
        splits.append(dict(event=ev,name=g['name'],kickoff_utc=dt.datetime.fromtimestamp(g['start'],dt.timezone.utc).isoformat(),score_first_arrival_delay_seconds=lag,live_observation_minutes=span,source_versions=len(plays),revisions=sum(p['reason']=='revision' for p in plays),excluded_first_arrivals=dict(collections.Counter(p['reason'] for p in plays if not p['clean'])),http_seconds=stats([r['duration'] for r in score]),poll_gap_seconds=stats(gaps),live_book_probes=stats(probes),operational_checks_pass=not reasons,blockers=reasons))
    blockers=[]
    if now-catalogue.get('at',0)>600:blockers.append('catalogue_stale')
    fees=store.meta('fees') or {}
    if now-fees.get('at',0)>600:blockers.append('fee_metadata_stale')
    if len(pairs)<cfg['verification_min_book_pairs']:blockers.append('insufficient_stable_book_comparisons')
    if len(mismatches)>cfg['verification_max_book_mismatches']:blockers.append('book_comparison_mismatch')
    if not any(g['operational_checks_pass'] for g in splits):blockers.append('no_game_has_verified_live_score_and_execution')
    qualified_proofs=[r for r in rows if r['mode']=='verify' and r['status'] in ('filled','settled')]
    if not qualified_proofs:blockers.append('qualified_signal_execution_not_yet_observed')
    clock=store.meta('clock') or {}
    if not clock.get('ntp') or now-clock.get('at',0)>45:blockers.append('clock_not_verified')
    if now>=ts(cfg['entry_cutoff']):blockers.append('weekend_entry_cutoff')
    modes={}
    for mode in ('verify','live'):
        equity=peak=drawdown=dec(0);part=[r for r in settled if r['mode']==mode]
        for r in sorted(part,key=lambda x:x['settled']):
            equity+=dec(r['net']);peak=max(peak,equity);drawdown=max(drawdown,peak-equity)
        modes[mode]=dict(simulated=mode=='verify',settled=len(part),net=str(equity),drawdown=str(drawdown),orders=dict(collections.Counter(r['status'] for r in rows if r['mode']==mode)))
    return dict(asof=now,config_hash=store.meta('config_hash'),games= splits,operational_checks_pass=not blockers,blockers=blockers,
                book_comparisons=dict(total=len(checks),stable=len(pairs),matches=len(pairs)-len(mismatches),mismatches=len(mismatches),unstable_excluded=len(checks)-len(pairs),http_and_probe_seconds=stats([r['body']['elapsed'] for r in checks])),
                results_by_mode=modes,
                decisions=dict(collections.Counter(r[0] for r in db.execute('SELECT reason FROM decisions'))),
                note='All verifier fills are simulated; no actual IOC acknowledgement or NFL fill latency has been established. First-arrival lag is relative to ESPN wallclock, not independent ground truth.')

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--root',type=Path,default=Path(__file__).resolve().parent);a=ap.parse_args()
    cfg=json.loads((a.root/'config.json').read_text());db=sqlite3.connect('file:'+str(a.root/'nfl.sqlite')+'?mode=ro',uri=True);db.row_factory=sqlite3.Row
    class Read:
        def meta(self,k):
            r=db.execute('SELECT value FROM meta WHERE key=?',(k,)).fetchone();return json.loads(r[0]) if r else None
    read=Read();read.db=db;r=build_report(read,cfg);out=a.root/'reports';out.mkdir(exist_ok=True)
    path=out/(dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'.json');path.write_text(json.dumps(r,indent=2));print(path);print(json.dumps(r,indent=2))
if __name__=='__main__':main()
