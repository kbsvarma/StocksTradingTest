"""Receipt audit of new unfilled paper attempts; no financial transport."""
from pathlib import Path
import datetime as dt
import hashlib
import json
import sqlite3
import sys
import zlib

R=Path(__file__).resolve().parent
P=R.parent.parent
sys.path.insert(0,str(P))
from engine import book, fee, ts
a=json.loads((R/'audit.json').read_text())
op=json.loads((R/'operations_audit.json').read_text())
cfg=json.loads((P/'frozen_config.json').read_text())
end=dt.datetime.fromisoformat(a['captured_utc']).timestamp()
db=sqlite3.connect((P/'paper.sqlite').as_uri()+'?mode=ro',uri=True)
db.row_factory=sqlite3.Row
db.execute('BEGIN')
def rows(q,args=()):return [dict(x) for x in db.execute(q,args)]
def receipt(rid):return rows('SELECT * FROM receipts WHERE id=?',(rid,))[0]
def payload(rid):
    r=receipt(rid)
    raw=zlib.decompress(db.execute('SELECT body FROM payloads WHERE hash=?',(r['hash'],)).fetchone()[0])
    assert hashlib.sha256(raw).hexdigest()==r['hash']
    return json.loads(raw)
out={'captured_utc':a['captured_utc'],'attempts':[],'same_signal_scenario_comparisons':[]}
for attempt in op['new_attempts']:
    p=json.loads(attempt['data']);d=rows('SELECT * FROM decisions WHERE id=?',(attempt['decision'],))[0]
    evidence=json.loads(d['data'])
    assert p['qty']==cfg['contracts']==5 and p['friction']==cfg['friction'][attempt['scenario']]*5
    assert p['cap']==evidence['pick']['cap'] and p['ticker']==evidence['pick']['ticker'] and p['side']==evidence['pick']['side']
    assert abs(attempt['reserve']-(5*p['cap']+fee(p['cap'],5)+p['friction']))<1e-9
    if attempt['scenario']!='canonical':
        assert abs(attempt['due']-attempt['created']-cfg['delays'][attempt['scenario']])<1e-6
    else:
        pub=rows('SELECT * FROM publications WHERE series=? AND source=? ORDER BY received,id LIMIT 1',(attempt['series'],p['source']['source_t']))[0]
        assert attempt['due']==pub['received']
    checks=rows('SELECT * FROM book_checks WHERE attempt=? ORDER BY phase',(attempt['id'],))
    assert attempt['status']=='unfilled' and attempt['reason']=='insufficient_depth_inside_cap'
    assert len(checks)==1 and checks[0]['phase']==0
    b=checks[0];r=receipt(b['receipt'])
    calc,why=book(next(x for x in payload(r['id'])['orderbooks'] if x['ticker']==p['ticker']),p['side'],p['cap'],5)
    assert why==b['reason']==attempt['reason'] and calc['depth']<5
    assert attempt['due']<=r['started']<=r['received']<=attempt['due']+8
    assert r['received']<p['close'] and r['status']==200 and not r['error'] and r['duration']<=5
    assert float(json.loads(r['headers']).get('Age') or 0)<=1
    mrid=json.loads(b['data'])['market_receipt'];mr=receipt(mrid)
    market=next(x for x in payload(mrid)['markets'] if x['ticker']==p['ticker'])
    assert market['status']=='active' and not market.get('settlement_ts') and ts(market['close_time'])>r['received']
    assert attempt['due']<=mr['started']<=mr['received']<=r['started']
    assert not rows('SELECT id FROM positions WHERE attempt=?',(attempt['id'],))
    at_signal,reason=book(next(x for x in payload(evidence['postbook_receipt'])['orderbooks'] if x['ticker']==p['ticker']),p['side'],p['cap'],5)
    out['attempts'].append({'attempt':attempt['id'],'decision':attempt['decision'],'scenario':attempt['scenario'],
        'ticker':p['ticker'],'side':p['side'],'qty':5,'signal_ask':p['ask'],'original_cap':p['cap'],
        'signal_book':at_signal,'signal_book_depth_reason':reason,'execution_book_receipt':r['id'],'execution_book':calc,
        'status':attempt['status'],'reason':why,'decision_to_book_seconds':r['received']-attempt['created'],
        'due_to_book_seconds':r['received']-attempt['due'],'market_status_receipt':mrid,'modeled_trade_pnl':0,
        'explanation':'First required book failed five-contract depth; second book is correctly not requested. No fill or fees booked.'})
for did in sorted({x['decision'] for x in op['new_attempts']}):
    d=rows('SELECT * FROM decisions WHERE id=?',(did,))[0];data=json.loads(d['data']);source=data['source']
    signal=next(x for x in a['qualified_signals'] if x['decision_id']==did)
    first=rows('SELECT * FROM sources WHERE series=? AND source=? ORDER BY received,id LIMIT 1',(d['series'],source['source_t']))[0]
    prior_source=json.loads(first['data'])['previous_poll']
    pub=rows('SELECT * FROM publications WHERE series=? AND source=? ORDER BY received,id LIMIT 1',(d['series'],source['source_t']))[0]
    pubr=receipt(pub['receipt'])
    prev=rows('SELECT * FROM receipts WHERE url=? AND received<? AND status=200 AND error IS NULL ORDER BY received DESC LIMIT 1',(pubr['url'],pubr['received']))[0]
    old=next((x for x in payload(prev['id'])['timeseries'] if x['t']/1000==source['source_t']),None)
    assert old is None or old.get('v') is None
    aa=[x for x in out['attempts'] if x['decision']==did]
    assert len(aa)==3 and {x['scenario'] for x in aa}=={'early','delay60','canonical'}
    out['same_signal_scenario_comparisons'].append({**signal,
        'source_observation_interval':[prior_source['started'],first['received']],
        'canonical_observation_interval':[prev['started'] if old else None,pubr['received']],
        'previous_canonical_poll_has_source_minute':old is not None,
        'observed_receipt_lead_seconds':pubr['received']-first['received'],
        'lead_interval_seconds':[prev['started']-first['received'] if old else None,pubr['received']-prior_source['started']],
        'all_scenarios_attempted':True,'same_original_decision':True,
        'outcomes':[{'scenario':x['scenario'],'attempt':x['attempt'],'status':x['status'],'net':0} for x in aa],
        'limitation':('Prior source-minute omission supplies no continuous publication lower bound.' if old is None else 'Bounds are polling observations; publication or correction between polls cannot be resolved, and source clocks are not independently verified.')})
assert not op['new_http_errors'] and not op['new_http_poll_gaps_over_45s'] and not op['new_complete_source_gaps_over_10min']
out['finding']='New LA signal failed the frozen five-contract depth rule in all scenarios. Early quote was still69c but only1 contract was displayed inside70c cap; later asks96c/98c. Expected unfilled execution, no newly identified plumbing failure or demonstrated five-contract opportunity.'
out['notification_decision']='DONT_NOTIFY: no new fills, settlements, milestone, repair or user action; retain this ordinary depth rejection for sample-wide analysis.'
db.close()
(R/'execution_addendum.json').write_text(json.dumps(out,indent=2)+'\n')
print(json.dumps(out,indent=2))
