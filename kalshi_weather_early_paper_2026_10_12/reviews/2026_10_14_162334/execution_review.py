"""Read-only audit of the next frozen signal and its three execution scenarios."""
from pathlib import Path
import collections
import datetime as dt
import hashlib
import json
import sqlite3
import sys
import zlib

R=Path(__file__).resolve().parent
P=R.parent.parent
sys.path.insert(0,str(P))
from engine import book,fee,ts
a=json.loads((R/'audit.json').read_text());op=json.loads((R/'operations_audit.json').read_text())
cfg=json.loads((P/'frozen_config.json').read_text())
end=dt.datetime.fromisoformat(a['captured_utc']).timestamp()
start=dt.datetime.fromisoformat(op['previous_review_utc']).timestamp()
db=sqlite3.connect((P/'paper.sqlite').as_uri()+'?mode=ro',uri=True);db.row_factory=sqlite3.Row;db.execute('BEGIN')
def rows(q,args=()):return [dict(x) for x in db.execute(q,args)]
def receipt(rid):return rows('SELECT * FROM receipts WHERE id=?',(rid,))[0]
def payload(rid):
    r=receipt(rid);raw=zlib.decompress(db.execute('SELECT body FROM payloads WHERE hash=?',(r['hash'],)).fetchone()[0])
    assert hashlib.sha256(raw).hexdigest()==r['hash']
    return json.loads(raw)
out={'captured_utc':a['captured_utc'],'new_attempts':[],'publication_pairings':[],'settlement_polling':[],'rate_limits':[]}
for attempt in op['new_attempts']:
    pick=json.loads(attempt['data']);decision=rows('SELECT * FROM decisions WHERE id=?',(attempt['decision'],))[0];data=json.loads(decision['data'])
    assert all(pick[k]==data['pick'][k] for k in ['ticker','side','cap','ask'])
    assert pick['qty']==5==cfg['contracts'] and pick['friction']==cfg['friction'][attempt['scenario']]*5
    assert abs(attempt['reserve']-(5*pick['cap']+fee(pick['cap'],5)+pick['friction']))<1e-9
    checks=rows('SELECT * FROM book_checks WHERE attempt=? AND received<=? ORDER BY phase',(attempt['id'],end))
    pub=rows('SELECT * FROM publications WHERE series=? AND source=? AND received<=? ORDER BY received,id LIMIT 1',(attempt['series'],pick['source']['source_t'],end))[0]
    item={'attempt':attempt['id'],'decision':attempt['decision'],'scenario':attempt['scenario'],
        'ticker':pick['ticker'],'side':pick['side'],'qty':pick['qty'],'signal_ask':pick['ask'],'cap':pick['cap'],
        'created':attempt['created'],'due':attempt['due'],'close':pick['close'],'status':attempt['status'],'reason':attempt['reason'],'checks':[]}
    if attempt['scenario']!='canonical':
        assert abs(attempt['due']-attempt['created']-cfg['delays'][attempt['scenario']])<1e-6
    for check in checks:
        r=receipt(check['receipt']);raw=next(x for x in payload(r['id'])['orderbooks'] if x['ticker']==pick['ticker'])
        calc,reason=book(raw,pick['side'],pick['cap'],5)
        assert reason==check['reason'] and calc==json.loads(check['data'])['book']
        assert attempt['due']<=r['started']<=r['received']<=attempt['due']+cfg['execution_deadline']
        assert r['received']<pick['close'] and r['status']==200 and not r['error'] and r['duration']<=cfg['max_http']
        assert float(json.loads(r['headers']).get('Age') or 0)<=1
        item['checks'].append({'phase':check['phase'],'receipt':r['id'],'started':r['started'],'received':r['received'],
            'book':calc,'reason':reason,'decision_to_book_seconds':r['received']-attempt['created']})
    pos=next((x for x in a['trades'] if x['attempt']==attempt['id']),None)
    if attempt['status']=='filled':
        assert pos and len(checks)==2 and all(not x['reason'] for x in item['checks'])
        assert item['checks'][1]['started']-item['checks'][0]['received']>=cfg['second_book_seconds']
        assert pos['price']==max(pick['ask'],*(x['book']['vwap'] for x in item['checks']))
        assert abs(pos['fee']-fee(pos['price'],5))<1e-9
        item['position_at_capture']=pos
    elif attempt['reason']=='insufficient_depth_inside_cap':
        assert pos is None and checks and item['checks'][-1]['book']['depth']<5
    elif attempt['reason']=='closed_before_execution':
        assert pos is None and not checks and attempt['scenario']=='canonical'
        assert pub['received']>=pick['close'] and attempt['due'] is None
        item['first_publication_after_expiry_seconds']=pub['received']-pick['close']
    else:
        raise AssertionError(('Unhandled execution state',attempt))
    out['new_attempts'].append(item)
for signal in a['qualified_signals']:
    d=rows('SELECT * FROM decisions WHERE id=?',(signal['decision_id'],))[0];source=json.loads(d['data'])['source']
    first=rows('SELECT * FROM sources WHERE series=? AND source=? ORDER BY received,id LIMIT 1',(d['series'],source['source_t']))[0]
    prior_source=json.loads(first['data'])['previous_poll']
    pub=rows('SELECT * FROM publications WHERE series=? AND source=? AND received<=? ORDER BY received,id LIMIT 1',(d['series'],source['source_t'],end))[0]
    pubr=receipt(pub['receipt']);previous=rows('SELECT * FROM receipts WHERE url=? AND received<? AND status=200 AND error IS NULL ORDER BY received DESC LIMIT 1',(pubr['url'],pubr['received']))[0]
    old=next((x for x in payload(previous['id'])['timeseries'] if x['t']/1000==source['source_t']),None)
    assert old is None or old.get('v') is None
    attempts=rows('SELECT id,scenario,status,reason FROM attempts WHERE decision=? ORDER BY id',(d['id'],))
    out['publication_pairings'].append({**signal,'previous_canonical_poll_has_source_minute':old is not None,
        'source_observation_interval':[prior_source['started'],first['received']],
        'canonical_observation_interval':[previous['started'] if old else None,pubr['received']],
        'lead_interval_seconds':[previous['started']-first['received'] if old else None,pubr['received']-prior_source['started']],
        'attempts':attempts,'all_three_attempted':all(x['status']!='blocked' for x in attempts),
        'limitation':'Polling bounds cannot establish exclusive information or exact source/publication clocks; source-minute omission supplies no continuous lower bound.'})
for position in [p for p in a['trades'] if p['settled'] is None]:
    rr=rows('SELECT * FROM receipts WHERE url LIKE ? AND received>=? AND received<=? ORDER BY received',('%/markets?tickers='+position['ticker']+'%',position['entered'],end))
    assert rr
    last=rr[-1];market=next(m for m in payload(last['id'])['markets'] if m['ticker']==position['ticker'])
    assert last['status']==200 and not last['error'] and not market.get('settlement_ts')
    out['settlement_polling'].append({'ticker':position['ticker'],'poll_count':len(rr),'last_receipt':last['id'],'last_received':last['received'],
        'last_poll_age':end-last['received'],'http_errors':sum(r['status']!=200 or r['error'] is not None for r in rr),
        'max_poll_gap':max((y['received']-x['received'] for x,y in zip(rr,rr[1:])),default=0),
        'authoritative_market_status':market.get('status'),'result':market.get('result'),'settlement_ts':market.get('settlement_ts'),
        'explanation':'Authoritative settlement absent at capture; no realized outcome or PnL credited.'})
for error in op['new_http_errors']:
    next_r=rows('SELECT id,started,received,status,url FROM receipts WHERE started>? AND received<=? ORDER BY started LIMIT 1',(error['received'],end))[0]
    recovered=rows('SELECT id,started,received,status FROM receipts WHERE url=? AND received>? AND received<=? AND status=200 AND error IS NULL ORDER BY received LIMIT 1',(error['url'],error['received'],end))[0]
    assert error['status']==429 and next_r['started']-error['received']>=30
    near=rows('SELECT id,scenario,created,status FROM attempts WHERE created BETWEEN ? AND ?',(error['received']-60,error['received']+180))
    assert not near
    out['rate_limits'].append({'error':error,'next_request':next_r,'cooldown_seconds':next_r['started']-error['received'],
        'same_url_recovery':recovered,'recovery_seconds':recovered['received']-error['received'],'nearby_attempts':near})
out['new_censored_source_versions']=[]
for r in rows('SELECT * FROM sources WHERE censored=1 AND received>? AND received<=?',(start,end)):
    first=rows('SELECT id,source,received,censored FROM sources WHERE series=? AND source=? ORDER BY received,id LIMIT 1',(r['series'],r['source']))[0]
    data=json.loads(r['data'])
    out['new_censored_source_versions'].append({'id':r['id'],'series':r['series'],'source':r['source'],'received':r['received'],
        'receipt':r['receipt'],'first_seen_new_minute':data['first_seen_new_minute'],'pending':data['pending'],'first_record':first})
out['publication_lower_bound_missing_count']=sum(not x['previous_canonical_poll_has_source_minute'] for x in out['publication_pairings'])
assert not op['new_complete_source_gaps_over_10min']
db.close()
(R/'execution_review.json').write_text(json.dumps(out,indent=2)+'\n')
print(json.dumps(out,indent=2))
