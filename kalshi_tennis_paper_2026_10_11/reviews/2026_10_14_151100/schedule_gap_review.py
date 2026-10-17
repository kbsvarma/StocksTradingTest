"""Receipt audit of a schedule pause and late opening scores; no runtime writes."""
from pathlib import Path
import bisect
import datetime as dt
import hashlib
import json
import sqlite3
import zlib
from engine import plays, point_key

P = Path(__file__).resolve().parent
db = sqlite3.connect((P/'paper_snapshot.sqlite').as_uri()+'?mode=ro&immutable=1', uri=True)
db.row_factory = sqlite3.Row
def rows(q, args=()): return [dict(x) for x in db.execute(q, args)]
evidence = {}
def payload(rid, keep=False):
    r = rows('SELECT * FROM receipts WHERE id=?', (rid,))[0]
    raw = zlib.decompress(db.execute('SELECT body FROM payloads WHERE hash=?', (r['hash'],)).fetchone()[0])
    assert hashlib.sha256(raw).hexdigest() == r['hash']
    result = json.loads(raw)
    if keep: evidence[str(rid)] = {'receipt': r, 'payload': result}
    return result

a = json.loads((P/'audit.json').read_text())
s = json.loads((P/'supplemental_audit.json').read_text())
assert len(a['issues']) == 1 and a['issues'][0]['kind'] == 'unexplained_poll_gap'
gap = a['issues'][0]['detail']
assert gap['event'] == 'KXWTAMATCH-26OCT13KRUTAR'
lo = dt.datetime.fromisoformat(gap['from_utc']).timestamp()
hi = dt.datetime.fromisoformat(gap['to_utc']).timestamp()
event = gap['event']
first = rows('SELECT MIN(first_seen) received,MIN(source) source FROM points WHERE event=?',(event,))[0]
assert first['received'] > hi > first['source'] > lo
snaps = rows('SELECT * FROM snapshots WHERE event=? AND received BETWEEN ? AND ? ORDER BY received',(event,lo-.000001,first['received']))
assert abs(snaps[0]['received']-lo)<.000001 and abs(snaps[1]['received']-hi)<.000001
assert all(not plays(payload(x['receipt'], True)) for x in snaps[:-1])
assert len(plays(payload(snaps[-1]['receipt'], True))) == 6
changes = rows("SELECT * FROM health WHERE event=? AND kind='schedule_changed' AND at BETWEEN ? AND ? ORDER BY at",(event,lo,hi))
assert len(changes) == 1
_, new_start = json.loads(changes[0]['detail'])
assert abs(hi-(new_start-120)) < 2
prior_seen = rows('SELECT COUNT(*) n FROM points WHERE event=? AND first_seen<=?',(event,lo))[0]['n']
assert prior_seen == 0
opening = rows('SELECT event,boundary,received,reason FROM decisions WHERE event=? AND boundary BETWEEN ? AND ? ORDER BY boundary',(event,lo,first['received']+180))
assert not any(x['reason']=='eligible' for x in opening)
assert not rows('SELECT id FROM attempts WHERE event=? AND created BETWEEN ? AND ?',(event,lo,first['received']+180))

# Conservatively distinguish a point whose provider timestamp predates the
# current continuous polling segment from measured latency within that segment.
# Keep every original point and original metric; this affects review only.
segments = {}
for x in rows('SELECT s.*,r.started FROM snapshots s JOIN receipts r ON r.id=s.receipt ORDER BY s.event,s.received,s.id'):
    ev = x['event']
    if ev not in segments: segments[ev] = []
    seq = segments[ev]
    start = x['started'] if not seq or x['received']-seq[-1][0] > 30 else seq[-1][1]
    seq.append((x['received'],start))
suspects = {(x['event'], point_key(v)) for x in s['point_key_removals'] for v in x['removed']+x['added']}
clean = rows('SELECT * FROM points WHERE censored=0 AND revision=0 AND upper_delay>=0')
clean = [x for x in clean if (x['event'],x['point_key']) not in suspects]
excluded = []
accepted = []
times = {k:[x[0] for x in v] for k,v in segments.items()}
for pt in clean:
    seq = segments[pt['event']]
    ix = bisect.bisect_right(times[pt['event']],pt['first_seen'])-1
    assert ix >= 0 and seq[ix][0] == pt['first_seen']
    segment_start = seq[ix][1]
    if pt['source'] < segment_start:
        excluded.append({**pt,'continuous_segment_start':segment_start})
    else:
        accepted.append(pt)
vals = sorted(x['upper_delay'] for x in accepted)
out = {
    'captured_utc':a['captured_utc'], 'explained_issues':a['issues'],
    'unresolved_issues':[], 'schedule_changes':changes,
    'first_point_provider_time':first['source'], 'first_point_received':first['received'],
    'gap':gap, 'snapshots_after_resume_before_first_points':len(snaps)-2,
    'first_seen_play_count':6, 'opening_decisions':opening,
    'finding':'Polling paused under the frozen schedule rule after the advertised start moved to11:10UTC. It resumed11:08UTC;12 successful snapshots still contained no plays, then6 plays first appeared11:10:03UTC. Two provider timestamps precede resumption. The initial audit compared polling end to hindsight provider time rather than first receipt. No previously received live score was abandoned; no known qualified signal was dropped. Possible observations during the scheduled pause cannot be reconstructed or assigned hypothetical PnL.',
    'original_clean_latency':s['latency_excluding_observed_key_replacements'],
    'post_gap_timestamp_exclusions':excluded,
    'conservative_continuous_poll_latency':{'n':len(vals),'p50':vals[int(.5*(len(vals)-1))],'p95':vals[int(.95*(len(vals)-1))],'max':max(vals),'over90':sum(v>90 for v in vals),'over120':sum(v>120 for v in vals)},
    'continuous_poll_points_over90':[x for x in accepted if x['upper_delay']>90],
    'limitation':'Provider time is unverified. Original metrics remain preserved; supplemental exclusion of provider timestamps before the current uninterrupted polling segment is reporting only. No strategy, raw receipt, point row or trade is altered.',
    'receipts':evidence,
}
(P/'schedule_gap_review.json').write_text(json.dumps(out,indent=2)+'\n')
print(json.dumps({k:v for k,v in out.items() if k!='receipts'},indent=2))
