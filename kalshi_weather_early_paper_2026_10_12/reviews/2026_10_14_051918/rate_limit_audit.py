"""Investigate a recovered public-data throttle; no strategy/ledger mutations."""
from pathlib import Path
import datetime as dt
import json
import sqlite3

R=Path(__file__).resolve().parent
P=R.parent.parent
op=json.loads((R/'operations_audit.json').read_text())
end=dt.datetime.fromisoformat(op['captured_utc']).timestamp()
start=dt.datetime.fromisoformat(op['previous_review_utc']).timestamp()
db=sqlite3.connect((P/'paper.sqlite').as_uri()+'?mode=ro',uri=True)
db.row_factory=sqlite3.Row
db.execute('BEGIN')
def rows(query,args=()):
    return [dict(x) for x in db.execute(query,args)]
out={'captured_utc':op['captured_utc'],'failures':[],'new_censored_versions':[]}
for error in op['new_http_errors']:
    receipt=rows('SELECT * FROM receipts WHERE id=?',(error['id'],))[0]
    recovery=rows('SELECT id,started,received,status,url FROM receipts WHERE url=? AND received>? AND received<=? AND status=200 AND error IS NULL ORDER BY received LIMIT 1',(error['url'],error['received'],end))[0]
    next_request=rows('SELECT id,started,received,status,url FROM receipts WHERE started>? AND received<=? ORDER BY started LIMIT 1',(error['received'],end))[0]
    assert error['status']==429 and next_request['started']-error['received']>=30
    assert recovery['received']-error['received']<60
    affected=rows('SELECT id,event,source,created,reason FROM decisions WHERE created BETWEEN ? AND ? ORDER BY created',(error['received'],error['received']+300))
    assert all(d['reason']=='no_edge_or_price' for d in affected)
    during=rows('SELECT id,scenario,status,created FROM attempts WHERE created BETWEEN ? AND ?',(error['received']-60,error['received']+300))
    assert not during
    out['failures'].append({'error':receipt,'same_url_recovery':recovery,'recovery_seconds':recovery['received']-error['received'],
        'first_request_after_cooldown':next_request,'cooldown_seconds':next_request['started']-error['received'],
        'subsequent_decisions_in_entry_window':affected,'nearby_attempts':during})
for row in rows('SELECT * FROM sources WHERE censored=1 AND received>? AND received<=? ORDER BY received',(start,end)):
    first=rows('SELECT id,source,received,censored FROM sources WHERE series=? AND source=? ORDER BY received,id LIMIT 1',(row['series'],row['source']))[0]
    data=json.loads(row['data'])
    out['new_censored_versions'].append({'id':row['id'],'series':row['series'],'source':row['source'],'received':row['received'],
        'receipt':row['receipt'],'first_record':first,'first_seen_new_minute':data['first_seen_new_minute'],'pending':data['pending'],'previous_poll':data['previous_poll']})
    assert not data['first_seen_new_minute'] and not data['pending'] and first['censored']==0
    assert first['received']<row['received']
out['classification']='Recovered expected rate-limit handling, without a demonstrated missed qualifying trade; no operational repair justified.'
out['limitations']=['No books/source arrivals were observed during the cooldown; earlier executable opportunities cannot be reconstructed or ruled out.',
    'Later rejected decisions are not counterfactual evidence that all earlier quotes would have failed.',
    'The four censored rows are later canonical versions of source minutes already observed uncensored; no newly observed minute was excluded by these rows.']
out['notification_decision']='DONT_NOTIFY: unchanged holdings/results and no new material finding; previously reported throttle mechanism recovered automatically.'
db.close()
(R/'rate_limit_audit.json').write_text(json.dumps(out,indent=2)+'\n')
print(json.dumps({'recovery_seconds':[x['recovery_seconds'] for x in out['failures']],
    'cooldown_seconds':[x['cooldown_seconds'] for x in out['failures']],
    'censored_versions_verified':len(out['new_censored_versions']),'classification':out['classification'],
    'notification_decision':out['notification_decision']},indent=2))
