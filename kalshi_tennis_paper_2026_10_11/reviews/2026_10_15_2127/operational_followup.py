"""Read-only operational detail for this review; no network or runtime changes."""
import datetime as dt, hashlib, json, sqlite3, subprocess, urllib.parse, zlib
from pathlib import Path

P=Path(__file__).resolve().parent;ROOT=P.parent.parent
audit=json.loads((P/'audit.json').read_text());cut=dt.datetime.fromisoformat(audit['previous_review']).timestamp()
db=sqlite3.connect((ROOT/'paper.sqlite').as_uri()+'?mode=ro',uri=True);db.row_factory=sqlite3.Row
db.execute('pragma query_only=on');db.execute('begin')
def rows(q,a=()):return [dict(r) for r in db.execute(q,a)]
def receipt(i):return rows('select * from receipts where id=?',(i,))[0]
def body(r):
    raw=zlib.decompress(db.execute('select body from payloads where hash=?',(r['hash'],)).fetchone()[0])
    assert hashlib.sha256(raw).hexdigest()==r['hash'];return json.loads(raw)
import sys
sys.path.insert(0,str(ROOT))
from engine import plays

def preservation():
    live=ROOT.parent/'kalshi_revalidation_2026_09_12/live_v1'
    return dict(live_source={x.name:hashlib.sha256(x.read_bytes()).hexdigest() for x in live.glob('*.py')},
        live_flags={x.name:hashlib.sha256(x.read_bytes()).hexdigest() for x in (live/'state').iterdir() if x.is_file() and x.name.startswith(('ARM','HALT'))},
        services=subprocess.check_output(['systemctl','--user','show','kalshi-crypto-z2-live.service','kalshi-dashboard.service','kalshi-execution-observer.service','kalshi-tennis90-paper.service','kalshi-resting-paper.service','-p','Id','-p','MainPID','-p','NRestarts','-p','ActiveState'],text=True))
before=preservation();gaps=[]
for g in audit['new_poll_gaps_over30']:
    prev,cur=receipt(g['previous']),receipt(g['current'])
    changes=rows("select * from health where kind='schedule_changed' and event=? and at between ? and ?",(g['event'],prev['received']-10,cur['received']))
    gaps.append(dict(**g,previous_plays=len(plays(body(prev))),resumed_plays=len(plays(body(cur))),changes=changes,
                     first_point_source=db.execute('select min(source) from points where event=?',(g['event'],)).fetchone()[0]))
missing=rows("select event,detail,count(*) retries from health where kind='candle_not_published' and at>? group by event,detail",(cut,))
errors=[]
for err in audit['new_http_failures']:
    q=urllib.parse.parse_qs(urllib.parse.urlparse(err['url']).query);boundary=int(q['end_ts'][0]) if 'end_ts'in q else None
    tickers=q.get('market_tickers',[''])[0].split(',');event=tickers[0].rsplit('-',1)[0] if tickers[0] else None
    later=rows('select * from receipts where url=? and id>? and id<=? order by id',(err['url'],err['id'],audit['receipt_max_id']))
    recovered=[]
    for r in later:
        if r['status']!=200 or r['error']:continue
        b=body(r);markets={m['market_ticker']:m for m in b.get('markets',[])}
        complete=all(any(c['end_period_ts']==boundary for c in markets.get(t,{}).get('candlesticks',[])) for t in tickers)
        recovered.append(dict(receipt=r['id'],received=r['received'],seconds_after_boundary=r['received']-boundary,complete_candles=complete))
    errors.append(dict(**err,event=event,boundary=boundary,recovered=recovered,decision=rows('select event,boundary,received,reason from decisions where event=? and boundary=?',(event,boundary))))
fees=[]
for series in ('KXATPMATCH','KXWTAMATCH'):
    r=rows('select * from receipts where url like ? and status=200 and error is null order by id desc limit 1',('%/series/'+series,))[0]
    s=body(r)['series'];assert s['fee_type']=='quadratic_with_maker_fees' and float(s['fee_multiplier'])==1
    fees.append(dict(series=series,receipt=r['id'],received=r['received'],fee_type=s['fee_type'],multiplier=s['fee_multiplier']))
transport=rows('select duration,status from receipts where received>? and id<=?',(cut,audit['receipt_max_id']))
durations=sorted(r['duration'] for r in transport)
out=dict(gaps=gaps,new_missing_candle_minutes=missing,http_recovery=errors,fees=fees,
    transport=dict(requests=len(transport),median=durations[len(durations)//2],p95=durations[int(.95*(len(durations)-1))],maximum=max(durations),over5=sum(x>5 for x in durations)))
after=preservation();assert before==after
out['preservation_before']=before;out['sources_flags_and_five_services_unchanged']=True
(P/'operational_followup.json').write_text(json.dumps(out,indent=2))
print(json.dumps({k:v for k,v in out.items() if k!='preservation_before'},indent=2))
