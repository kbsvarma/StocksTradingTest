"""Read-only crypto decision/ledger diagnostic. No order client or mutations."""
import collections, datetime as dt, hashlib, json, math, sqlite3, statistics, subprocess, time
from pathlib import Path
from zoneinfo import ZoneInfo

P=Path(__file__).resolve().parent
L=P.parent/'kalshi_revalidation_2026_09_12/live_v1'
ET=ZoneInfo('America/New_York')
ASSETS=('BTC','ETH','SOL','XRP')
LANES=('cushion_z2','premium_z3')
def ts(day,hm='00:00'):
    return dt.datetime.fromisoformat(day+'T'+hm).replace(tzinfo=ET).timestamp()
def stamp(value):return dt.datetime.fromtimestamp(value,ET).isoformat()
START=ts('2026-10-03');END=int(time.time()//60)*60
def preserve():
    return dict(hashes={f.name:hashlib.sha256(f.read_bytes()).hexdigest() for f in L.iterdir() if f.suffix=='.py'},
        flags={f.name:hashlib.sha256(f.read_bytes()).hexdigest() for f in (L/'state').iterdir() if f.is_file() and f.name.startswith(('ARM','HALT'))},
        services={n:subprocess.check_output(['systemctl','--user','show',n,'-p','MainPID','-p','NRestarts','-p','ActiveState'],text=True) for n in ('kalshi-crypto-z2-live.service','kalshi-dashboard.service','kalshi-execution-observer.service')})
before=preserve(); status=json.loads((L/'state/status.json').read_text())
db=sqlite3.connect((L/'state/orders.sqlite3').as_uri()+'?mode=ro',uri=True);db.execute('pragma query_only=on')
orders=[json.loads(x[0]) for x in db.execute('select record from orders')]
attempts=[json.loads(x[0]) for x in db.execute('select record from order_attempts')]
events=[]
for ident,saved,raw in db.execute('select id,ts,event from events where ts>=? and ts<? order by id',(START,END+60)):
    r=json.loads(raw)
    if r.get('asset') in ASSETS and START<=r.get('decision_ts',0)<END:
        r['_event_id']=ident;r['_saved_ts']=saved;events.append(r)
db.close()
(P/'events.json').write_text(json.dumps(events))
(P/'orders.json').write_text(json.dumps(orders))
(P/'attempts.json').write_text(json.dumps(attempts))
(P/'status.json').write_text(json.dumps(status))
owned={'already_entered','already_paper_entered','position_filled','position_settled','zero_fill_locked','order_pending','order_rejected'}
data_errors={'data_error','incomplete_spot','decision_candle_unavailable','no_decision_quote'}
def qualified(r):
    if r.get('status') in owned:return False
    try:
        floor,ceiling=(.85,.95) if r['lane']=='cushion_z2' else (.93,.98)
        return floor-1e-8<=r['signal_ask']<ceiling-1e-8 and r['signal_ask']-r['signal_bid']<=.02000001 and 3<=r['remaining_minutes']<=10 and r['z']>=r.get('required_z',3 if r['lane']=='premium_z3' else 2)
    except (KeyError,TypeError):return False
groups=collections.defaultdict(list)
for r in events:groups[(r['asset'],r.get('lane','cushion_z2'),r['decision_ts'])].append(r)
flat=[]
for (asset,lane,T),rs in groups.items():
    qs=[r for r in rs if qualified(r)]
    usable=[r for r in rs if r.get('status') not in data_errors]
    r=qs[0] if qs else (usable[0] if usable else rs[-1])
    category=r.get('status')
    if qs:category='qualified'
    elif category in owned:category='already_owned'
    elif category=='cushion_gate':category='cushion'
    elif category=='price_gate':
        floor,ceiling=(.85,.95) if lane=='cushion_z2' else (.93,.98)
        category='price_below_floor' if r['signal_ask']<floor-1e-8 else ('price_above_ceiling' if r['signal_ask']>=ceiling-1e-8 else 'spread_only')
    flat.append(dict(asset=asset,lane=lane,T=T,category=category,record=r,
                     observations=len(rs),recovered_data_error=bool(usable) and any(x.get('status') in data_errors for x in rs),
                     qualified_rows=qs))
by_market={r['ticker']:r for r in orders}
def quant(xs):
    xs=sorted(xs)
    return dict(n=len(xs),median=statistics.median(xs),maximum=xs[-1],minimum=xs[0]) if xs else dict(n=0)
def summarize(a,b,asset=None,lane=None):
    rows=[x for x in flat if a<=x['T']<b and (asset is None or x['asset']==asset) and (lane is None or x['lane']==lane)]
    cats=dict(collections.Counter(x['category'] for x in rows));q=[x for x in rows if x['category']=='qualified']
    zrows=[x['record'] for x in rows if x['category'] in ('cushion','qualified')]
    qs=sorted({x['record']['ticker'] for x in q});details=[]
    for ticker in qs:
        o=by_market.get(ticker,{});obs=[r for x in q if x['record']['ticker']==ticker for r in x['qualified_rows']]
        details.append(dict(ticker=ticker,filled=float(o.get('filled',0))>0,order_status=o.get('status'),entry=o.get('entry'),
            signal_first=stamp(min(r['decision_ts'] for r in obs)),rows=len(obs),
            execution_statuses=dict(collections.Counter(r.get('execution_status',r.get('status')) for r in obs)),
            min_book_ask=min([r['observed_ask'] for r in obs if isinstance(r.get('observed_ask'),(int,float))],default=None)))
    fills=[r for r in orders if r.get('asset') in ASSETS and a<=r.get('created_ts',0)<b and float(r.get('filled',0))>0 and (asset is None or r['asset']==asset) and (lane is None or r.get('lane')==lane)]
    raw=[r for r in events if a<=r['decision_ts']<b and (asset is None or r['asset']==asset) and (lane is None or r['lane']==lane)]
    expected=int((b-a)/60)*(1 if asset else 4)*(1 if lane else 2)
    return dict(begin=stamp(a),end=stamp(b),scan_minutes=len(rows),expected=expected,missing=expected-len(rows),categories=cats,
        qualified_markets=len(qs),qualified_lane_minutes=len(q),filled_trades=len(fills),filled_assets=dict(collections.Counter(r['asset'] for r in fills)),
        attempted_markets=len({r['ticker'] for r in attempts if r.get('asset') in ASSETS and a<=r.get('created_ts',0)<b and (asset is None or r['asset']==asset) and (lane is None or r.get('lane')==lane)}),
        recovered_errors=sum(x['recovered_data_error'] for x in rows),z=quant([r['z'] for r in zrows]),
        sigma=quant([r['sigma'] for r in zrows]),abs_move_pct=quant([100*abs(r['spot']/r['strike_proxy']-1) for r in zrows]),
        scan_delay_ms=quant([1000*(r['_saved_ts']-r['decision_ts']) for r in raw if r.get('execution_check_number',1)==1]),
        markets=details)
days=[f'2026-10-{d:02}' for d in range(3,16)]
full_days={d:summarize(ts(d),min(END,ts(d)+86400)) for d in days}
matched={d:summarize(ts(d,'05:00'),ts(d,'10:10')) for d in days}
recent=summarize(ts('2026-10-15','01:54'),END)
post=summarize(ts('2026-10-15','07:35'),END)
asset_recent={a:{l:summarize(ts('2026-10-15','01:54'),END,a,l) for l in LANES} for a in ASSETS}
latest={a:sorted([r for r in orders if r.get('asset')==a and float(r.get('filled',0))>0],key=lambda r:r['created_ts'])[-1] for a in ASSETS}
closest=[];btc_removed=[];samples=[]
for a in ASSETS:
    for lane in LANES:
        rs=[x['record'] for x in flat if x['asset']==a and x['lane']==lane and ts('2026-10-15','01:54')<=x['T']<END and x['category']=='cushion']
        if rs:
            r=max(rs,key=lambda r:r['z']/r['required_z'])
            closest.append(r);samples.append(r)
            samples.append(max(rs,key=lambda r:r['decision_ts']))
        if a=='BTC' and lane=='cushion_z2':btc_removed=[r for r in rs if 2<=r['z']<3]
after=preserve();assert before==after
deployment=json.loads((P.parent/'kalshi_ws_fill_fix_2026_10_15/receipts/deployment_receipt.json').read_text())
changed=[n for n,h in deployment['source_hashes'].items() if n.endswith('.py') and before['hashes'].get(n)!=h]
assert not changed,changed
report=dict(captured=time.time(),cutoff=stamp(END),full_days=full_days,matched_0500_1010=matched,
    since_last_crypto_fill=recent,since_repair=post,asset_since_last=asset_recent,
    latest_fills={a:{k:r.get(k) for k in ('ticker','asset','lane','created_ts','side','filled','entry','pnl')} for a,r in latest.items()},
    closest=closest,btc_z2_only_minutes=len(btc_removed),btc_z2_only_markets=sorted({r['ticker'] for r in btc_removed}),
    source_changed_since_repair=changed,preservation_before=before,preservation_after=after)
(P/'classified.json').write_text(json.dumps(flat))
(P/'samples.json').write_text(json.dumps(list({(r['asset'],r['decision_ts']):r for r in samples}.values()),indent=2))
(P/'audit.json').write_text(json.dumps(report,indent=2))
print(json.dumps(dict(cutoff=report['cutoff'],days={k:{q:v[q] for q in ['scan_minutes','expected','missing','qualified_markets','filled_trades','categories','recovered_errors']} for k,v in full_days.items()}),indent=2))
