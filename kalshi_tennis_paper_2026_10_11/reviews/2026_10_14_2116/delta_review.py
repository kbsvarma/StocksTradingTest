"""Consistent read-only incremental tennis review. No network or runtime edits."""
import bisect,collections,datetime as dt,hashlib,json,sqlite3,subprocess,sys,time,zlib
from decimal import Decimal as D,ROUND_CEILING
from pathlib import Path
P=Path(__file__).resolve().parent;ROOT=P.parent.parent
assert ROOT==Path('/home/varma/kalshi_tennis_paper_2026_10_11')
sys.path.insert(0,str(ROOT))
from engine import canonical,digest,plays,point_key,book_fill,payout,day
prior=json.loads((ROOT/'review_state.json').read_text());cut=dt.datetime.fromisoformat(prior['last_checked_utc']).timestamp()
cfg=json.loads((ROOT/'frozen_config.json').read_text());manifest=json.loads((ROOT/'deployment_manifest.json').read_text())
def hashes():return {n:hashlib.sha256((ROOT/n).read_bytes()).hexdigest() for n in manifest}
assert hashes()==manifest
db=sqlite3.connect((ROOT/'paper.sqlite').as_uri()+'?mode=ro',uri=True,timeout=2);db.row_factory=sqlite3.Row
db.execute('PRAGMA query_only=ON');db.execute('BEGIN');now=time.time()
def rows(sql,args=()):return [dict(x) for x in db.execute(sql,args)]
cache={}
def body(h):
    if h not in cache:
        raw=zlib.decompress(db.execute('SELECT body FROM payloads WHERE hash=?',(h,)).fetchone()[0]);assert hashlib.sha256(raw).hexdigest()==h;cache[h]=json.loads(raw)
    return cache[h]
def receipt(i):return rows('SELECT * FROM receipts WHERE id=?',(i,))[0]
def quantiles(vals):
    vals=sorted(vals);return dict(n=len(vals),median=vals[int(.5*(len(vals)-1))] if vals else None,p95=vals[int(.95*(len(vals)-1))] if vals else None,max=max(vals) if vals else None,over90=sum(x>90 for x in vals),over120=sum(x>120 for x in vals))
meta={r['key']:json.loads(r['value']) for r in rows('SELECT * FROM meta')};assert digest(cfg)==meta['config_hash']
positions=rows('SELECT * FROM positions ORDER BY settled,entered');attempts={r['id']:r for r in rows('SELECT * FROM attempts')};events={r['event']:r for r in rows('SELECT * FROM events')}
decisions=rows("SELECT * FROM decisions WHERE reason='eligible' ORDER BY boundary,event")
checks=[]
for p in positions:
    a=attempts[p['attempt']];pick=json.loads(a['data']);bb=rows('SELECT * FROM book_checks WHERE attempt=? ORDER BY phase',(a['id'],));assert len(bb)==2
    books=[]
    for b in bb:
        r=receipt(b['receipt']);fill,reason=book_fill(body(r['hash']),pick['side'],pick['cap']);assert not reason and fill==json.loads(b['data'])
        assert a['due']<=r['started']<=r['received']<=a['due']+cfg['book_timeout'] and r['duration']<=cfg['max_http_seconds']
        age=json.loads(r['headers']).get('Age');assert age is None or float(age)<=1
        books.append((r,fill))
    assert books[1][0]['started']-books[0][0]['received']>=cfg['second_book_delay']
    assert p['price']==max(pick['ask'],*(b[1]['vwap'] for b in books)) and p['price']<=pick['cap']
    expected=(D('.07')*D(str(pick['mult']))*D(str(p['price']))*(1-D(str(p['price'])))).quantize(D('.0001'),rounding=ROUND_CEILING)
    assert expected==D(str(p['fee']))
    assert abs(D(str(p['cost']))-D(str(p['price']))-expected-D(str(p['friction'])))<D('1e-12')
    if p['settled']:
        settlement=body(receipt(p['settlement_receipt'])['hash'])['market'];assert payout(settlement,p['side'])==p['payout']
        assert abs(D(str(p['net']))-D(str(p['payout']))+D(str(p['cost'])))<D('1e-12')
    checks.append(dict(event=p['event'],scenario=p['scenario'],price=p['price'],net=p['net'],minimum_depth=min(x[1]['available'] for x in books),decision_to_fill=p['entered']-a['created']))
unfilled=[]
for a in attempts.values():
    if a['status']!='unfilled':continue
    b=rows('SELECT * FROM book_checks WHERE attempt=? ORDER BY phase DESC LIMIT 1',(a['id'],))[0];pick=json.loads(a['data']);fill,reason=book_fill(body(receipt(b['receipt'])['hash']),pick['side'],pick['cap']);assert fill is None and reason==a['reason']==b['reason'];unfilled.append(a['id'])
selected=set();ownership=0
for d in decisions:
    data=json.loads(d['data']);chosen=next(e for e in plays(body(data['snapshot']['hash'])) if e.get('event_type')=='point' and float(e['wall_clock'])==float(data['pick']['source_t']))
    selected.add((d['event'],point_key(chosen)))
    assert data['snapshot']['received']<=d['boundary'] and d['boundary']-float(chosen['wall_clock'])>=90
    for scenario in ('base','stress'):
        aa=any(a['event']==d['event'] and a['scenario']==scenario and a['boundary']==d['boundary'] for a in attempts.values())
        owned=any(p['event']==d['event'] and p['scenario']==scenario and p['entered']<=d['received'] for p in positions)
        pending=rows('''SELECT a.id FROM attempts a LEFT JOIN positions p ON p.attempt=a.id
                       WHERE a.event=? AND a.scenario=? AND a.boundary<? AND a.created<=?
                       AND (a.due>=? OR p.entered>? OR
                            (SELECT MAX(received) FROM book_checks WHERE attempt=a.id)>?)''',
                     (d['event'],scenario,d['boundary'],d['received'],d['received'],d['received'],d['received']))
        assert aa or owned or pending,(d['event'],d['boundary'],scenario)
        if not aa:
            assert rows("SELECT id FROM health WHERE event=? AND kind='portfolio_skip' AND detail=? AND at BETWEEN ? AND ?",(d['event'],scenario+':owned_or_pending',d['received'],d['received']+1))
        ownership+=1
newpoints=rows('SELECT * FROM points WHERE first_seen>? ORDER BY first_seen',(cut,))
new_selected_corrections=[x for x in newpoints if x['revision'] and (x['event'],x['point_key']) in selected]
previous_supp=json.loads((ROOT/'reviews/2026_10_14_151100/supplemental_audit.json').read_text())
suspects={(x['event'],point_key(v)) for x in previous_supp['point_key_removals'] for v in x['removed']+x['added']}
new_removals=[];new_empty=[];new_gaps=[]
for ev in {r['event'] for r in rows('SELECT DISTINCT event FROM snapshots WHERE received>?',(cut,))}:
    before=rows('SELECT * FROM snapshots WHERE event=? AND received<=? ORDER BY received DESC LIMIT 1',(ev,cut))
    snaps=before+rows('SELECT * FROM snapshots WHERE event=? AND received>? ORDER BY received,id',(ev,cut))
    for prev,cur in zip(snaps,snaps[1:]):
        old={point_key(x):x for x in plays(body(prev['hash']))};new={point_key(x):x for x in plays(body(cur['hash']))}
        if not body(cur['hash']) and old:new_empty.append(dict(event=ev,receipt=cur['receipt']))
        removed=old.keys()-new.keys();added=new.keys()-old.keys()
        if removed:
            suspects.update((ev,k) for k in removed|added);new_removals.append(dict(event=ev,receipt=cur['receipt'],removed=list(removed),added=list(added)))
        gap=cur['received']-prev['received']
        if gap>30:new_gaps.append(dict(event=ev,seconds=gap,previous=prev['receipt'],current=cur['receipt']))
allpoints=rows('SELECT * FROM points');segments=collections.defaultdict(list)
for s in rows('SELECT s.event,s.received,r.started FROM snapshots s JOIN receipts r ON r.id=s.receipt ORDER BY s.event,s.received,s.id'):
    seq=segments[s['event']];segment=s['started'] if not seq or s['received']-seq[-1][0]>30 else seq[-1][1];seq.append((s['received'],segment))
times={k:[x[0] for x in seq] for k,seq in segments.items()};vals=[];continuity_excluded=[]
for p in allpoints:
    if p['censored'] or p['revision'] or p['upper_delay']<0 or (p['event'],p['point_key']) in suspects:continue
    i=bisect.bisect_right(times[p['event']],p['first_seen'])-1
    if p['source']<segments[p['event']][i][1]:continuity_excluded.append(p)
    else:vals.append(p['upper_delay'])
newfail=rows('SELECT id,url,received,status,error FROM receipts WHERE received>? AND (error IS NOT NULL OR status!=200)',(cut,))
newhealth=rows('SELECT * FROM health WHERE at>? ORDER BY at',(cut,))
portfolios={}
for scenario in ('base','stress'):
    pp=[p for p in positions if p['scenario']==scenario];settled=[p for p in pp if p['settled']];aa=[a for a in attempts.values() if a['scenario']==scenario]
    portfolios[scenario]=dict(attempts=len(aa),attempted_matches=len({a['event'] for a in aa}),fills=len(pp),settled=len(settled),wins=sum(p['payout']==1 for p in settled),losses=sum(p['payout']==0 for p in settled),net=round(sum(p['net'] for p in settled),4),fees=round(sum(p['fee'] for p in settled),4),friction=round(sum(p['friction'] for p in settled),4),open=len(pp)-len(settled),pending=sum(a['status']=='pending' for a in aa),unfilled=collections.Counter(a['reason'] for a in aa if a['status']=='unfilled'),by_tour={tour:dict(trades=sum(events[p['event']]['series']==tour for p in pp),net=round(sum(p['net'] for p in settled if events[p['event']]['series']==tour),4)) for tour in cfg['series']})
upcoming=[dict(event=e['event'],start=e['start'],prior=json.loads(e['prior']) if e['prior'] else None,last_poll=e['last_poll'],currently_active=e['start']-120<=now<=e['start']+43200,prior_window_due=any(e['start']-minutes*60<=now<=e['start']-minutes*60+20 for minutes in (30,5))) for e in events.values() if not e['done']]
report=dict(captured_utc=dt.datetime.fromtimestamp(now,dt.timezone.utc).isoformat(),previous_review=prior['last_checked_utc'],mode='CONSISTENT_READ_ONLY_DELTA_NO_SNAPSHOT_COPY',portfolios=portfolios,health=dict(heartbeat_age=now-meta['heartbeat'],catalogue_age=now-meta['catalogue_last_success'],clock=meta['clock'],elapsed_days=(now-meta['started'])/86400),qualified_decisions=len(decisions),ownership_paths_checked=ownership,position_checks=checks,unfilled_checked=unfilled,new_points=newpoints,new_selected_corrections=new_selected_corrections,new_selected_replacements=[list(x) for x in suspects & selected],new_snapshot_removals=new_removals,new_empty_snapshots=new_empty,new_poll_gaps_over30=new_gaps,conservative_continuous_latency=quantiles(vals),continuity_exclusions=continuity_excluded,total_point_versions=len(allpoints),total_revisions=sum(x['revision'] for x in allpoints),new_http_failures=newfail,new_health=newhealth,new_attempts=[a for a in attempts.values() if a['created']>cut],new_settlements=[p for p in positions if p['settled'] and p['settled']>cut],upcoming=upcoming,receipt_max_id=db.execute('SELECT max(id) FROM receipts').fetchone()[0],table_hashes={name:hashlib.sha256(canonical(rows('SELECT * FROM '+name+' ORDER BY '+order)).encode()).hexdigest() for name,order in [('positions','id'),('attempts','id'),('decisions','event,boundary'),('book_checks','id')]})
db.close()
assert not new_selected_corrections and not report['new_selected_replacements']
assert hashes()==manifest
service=subprocess.check_output(['systemctl','--user','show','kalshi-tennis90-paper.service','-p','ActiveState','-p','MainPID','-p','NRestarts'],text=True)
report['service']=service;report['ntp']=subprocess.check_output(['timedatectl','show','-p','NTPSynchronized','--value'],text=True).strip()
assert 'ActiveState=active' in service and 'MainPID=4015560' in service and 'NRestarts=0' in service
assert report['ntp']=='yes' and report['health']['heartbeat_age']<10 and report['health']['catalogue_age']<120
assert not report['new_attempts'] and not report['new_settlements'] and not newfail and not new_gaps and not new_empty
assert not any(x['currently_active'] or x['prior_window_due'] for x in upcoming)
assert portfolios['base']['settled']==prior['last_base_settled']==30 and portfolios['stress']['settled']==prior['last_stress_settled']==26
(P/'prior_review_state.json').write_text(json.dumps(prior,indent=2)+'\n');(P/'audit.json').write_text(json.dumps(report,indent=2)+'\n')
lines=['TENNIS PAPER REVIEW — unchanged, DONT_NOTIFY',report['captured_utc'],'', 'Service active; clock synchronized; frozen deployment hashes unchanged. No runtime edits or restarts.',f"Normal:30 settled,12W18L,net -$1.8638 after $0.4640 fees and $0.3000 friction.",f"60s delayed:26 settled,7W19L,net -$5.1116 after $0.3917 fees and $0.5200 friction.",'No new attempts, fills or settlements since the previous review. Both scenarios flat with no pending paper orders.',f"Revalidated all{len(positions)} fills and settlements, both subsequent books/caps, fees/friction; all{len(decisions)} eligible decisions and{ownership} scenario ownership paths; all{len(unfilled)} unfilled cap failures.",f"New point versions:{len(newpoints)}, revisions:{sum(p['revision'] for p in newpoints)}. No selected point correction/replacement affecting an eligible decision.",f"Conservative continuous-poll delay:{json.dumps(report['conservative_continuous_latency'])}.",'No new HTTP failures, empty score payload episodes or polling gaps over30s. No currently active matches or due prospective-prior windows; next advertised match is tonight.',f"Elapsed{report['health']['elapsed_days']:.3f}days. First20 preliminary report already sent;14day/100normal settlement gate unmet. No proven edge; both results remain negative.",'Audit uses one short read-only SQLite transaction; no repeated database copy. Original receipts and all losing records preserved. Review state updated; no user notification warranted.']
(P/'REPORT.txt').write_text('\n'.join(lines)+'\n')
state=dict(prior);state.update(last_checked_utc=report['captured_utc'],last_artifact=f'reviews/{P.name}/audit.json',last_report=f'reviews/{P.name}/REPORT.txt',checks_completed=prior['checks_completed']+1,last_operational_findings=lines[3:11],last_disposition='DONT_NOTIFY: unchanged trades and PnL, healthy idle schedule, no new operational failure or milestone.',review_completed_utc=dt.datetime.now(dt.timezone.utc).isoformat())
assert json.loads((ROOT/'review_state.json').read_text())==prior
text=json.dumps(state,indent=2)+'\n';(P/'review_state.json').write_text(text);tmp=ROOT/'review_state.json.tmp';tmp.write_text(text);tmp.replace(ROOT/'review_state.json')
(ROOT/'latest_review.json').write_text(json.dumps(dict(directory=str(P),captured_utc=report['captured_utc'],status='complete',decision='DONT_NOTIFY'),indent=2)+'\n')
print(json.dumps({k:report[k] for k in ('captured_utc','portfolios','health','conservative_continuous_latency','total_point_versions','total_revisions','new_http_failures','new_poll_gaps_over30','service')}))
