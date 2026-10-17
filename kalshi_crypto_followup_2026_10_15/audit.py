"""Read-only follow-up: observed signals, real orders, health and preservation."""
import collections, datetime, hashlib, json, math, sqlite3, statistics, subprocess, time
from pathlib import Path
from zoneinfo import ZoneInfo
P=Path(__file__).resolve().parent
L=P.parent/'kalshi_revalidation_2026_09_12/live_v1'
ET=ZoneInfo('America/New_York')
A=('BTC','ETH','SOL','XRP'); LANES=('cushion_z2','premium_z3')
prior=json.loads((P.parent/'kalshi_crypto_lull_deep_2026_10_15/final_preservation.json').read_text())
since=prior['ts']; start=math.ceil(since/60)*60; end=int(time.time()//60)*60
def et(t):return datetime.datetime.fromtimestamp(t,ET).isoformat()
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def preserve():
    return dict(source={p.name:sha(p) for p in L.glob('*.py')},
        flags={p.name:sha(p) for p in (L/'state').iterdir() if p.name.startswith(('ARM','HALT'))},
        services=subprocess.check_output(['systemctl','--user','show','kalshi-crypto-z2-live.service','kalshi-dashboard.service','kalshi-execution-observer.service','kalshi-tennis90-paper.service','kalshi-resting-paper.service','-p','Id','-p','MainPID','-p','ActiveState','-p','NRestarts'],text=True))
before=preserve()
expected=json.loads((P.parent/'kalshi_ui_hide_usdcad_2026_10_15/deployment.json').read_text())['after']
assert before['source']==expected['source']
assert before['flags']==expected['flags']
db=sqlite3.connect((L/'state/orders.sqlite3').as_uri()+'?mode=ro',uri=True)
db.execute('PRAGMA query_only=ON');db.execute('BEGIN')
orders=[json.loads(r[0]) for r in db.execute('select record from orders')]
attempts=[json.loads(r[0]) for r in db.execute('select record from order_attempts')]
events=[]
for i,t,raw in db.execute('select id,ts,event from events where ts>=? and ts<? order by id',(start,end+60)):
    r=json.loads(raw)
    if r.get('asset') in A and start<=r.get('decision_ts',0)<end:
        r.update(_event_id=i,_saved_ts=t);events.append(r)
db.close()
status=json.loads((L/'state/status.json').read_text())
bykey=collections.defaultdict(list)
for r in events:bykey[(r['asset'],r['lane'],r['decision_ts'])].append(r)
owned={'already_entered','already_paper_entered','position_filled','position_settled','zero_fill_locked','order_pending','order_rejected'}
errors={'data_error','incomplete_spot','decision_candle_unavailable','no_decision_quote'}
def qualified(r):
    if r.get('status') in owned:return False
    try:
        lo,hi=(.85,.95) if r['lane']=='cushion_z2' else (.93,.98)
        return lo-1e-8<=r['signal_ask']<hi-1e-8 and r['signal_ask']-r['signal_bid']<=.02000001 and 3<=r['remaining_minutes']<=10 and r['z']>=r['required_z']
    except (KeyError,TypeError):return False
flat=[]
for (a,l,t),rs in bykey.items():
    qs=[r for r in rs if qualified(r)]
    usable=[r for r in rs if r.get('status') not in errors]
    r=qs[0] if qs else usable[0] if usable else rs[-1]
    cat=r.get('status')
    if qs:cat='qualified'
    elif cat in owned:cat='owned_or_attempted'
    elif cat=='price_gate':
        lo,hi=(.85,.95) if l=='cushion_z2' else (.93,.98)
        cat='below_price' if r['signal_ask']<lo-1e-8 else 'above_price' if r['signal_ask']>=hi-1e-8 else 'spread'
    flat.append(dict(asset=a,lane=l,ts=t,category=cat,record=r,recovered_data_error=bool(usable) and any(x.get('status') in errors for x in rs)))
qevents=[r for r in events if qualified(r)]
markets=[]
byorder={r['ticker']:r for r in orders}
for ticker in sorted({r['ticker'] for r in qevents}):
    rs=[r for r in qevents if r['ticker']==ticker];o=byorder.get(ticker,{})
    markets.append(dict(ticker=ticker,asset=rs[0]['asset'],first=et(min(r['decision_ts'] for r in rs)),
        status=o.get('status'),filled=o.get('filled'),entry=o.get('entry'),pnl=o.get('pnl'),
        qualified_lane_minutes=len({(r['lane'],r['decision_ts']) for r in rs}),
        execution=dict(collections.Counter(r.get('execution_status',r['status']) for r in rs)),
        executable_checks=sum(bool(r.get('execution_executable')) for r in rs),
        book_transports=dict(collections.Counter((r.get('book_transport') or {}).get('source','none') for r in rs))))
new=[r for r in orders if r.get('asset') in A and r.get('created_ts',0)>since]
def compact(r):
    out={k:r.get(k) for k in ('ticker','asset','lane','status','created_ts','side','filled','entry','cost','fees','pnl','attempt_number','initial_fill_transport','fill_transport','fill_confirmed_ts')}
    out['time_et']=et(r['created_ts']);out['signal']=r.get('signal');return out
def quant(xs):
    xs=sorted(xs)
    return dict(n=len(xs),median=statistics.median(xs),p95=xs[min(len(xs)-1,int(.95*len(xs)))],max=max(xs)) if xs else dict(n=0)
latest={a:compact(max((r for r in orders if r.get('asset')==a and float(r.get('filled',0))>0),key=lambda r:r['created_ts'])) for a in A}
expected_keys={(a,l,t) for a in A for l in LANES for t in range(start,end,60)}
missing=sorted(expected_keys-set(bykey))
closest=[]
for a in A:
    for l in LANES:
        rs=[x['record'] for x in flat if x['asset']==a and x['lane']==l and x['category']=='cushion_gate']
        if rs:closest.append(max(rs,key=lambda r:r['z']/r['required_z']))
formula=[]
for x in flat:
    r=x['record']
    if all(r.get(k) is not None for k in ('spot','strike_proxy','sigma','remaining_minutes','z')):
        z=(1 if r['side']=='yes' else -1)*(r['spot']/r['strike_proxy']-1)/(r['sigma']*math.sqrt(r['remaining_minutes']))
        formula.append(abs(z-r['z']))
after=preserve();assert before==after
out=dict(captured=time.time(),previous_check=et(since),period=[et(start),et(end)],checks=len(bykey),expected=len(expected_keys),missing=missing,
    categories=dict(collections.Counter(x['category'] for x in flat)),
    by_asset={a:dict(collections.Counter(x['category'] for x in flat if x['asset']==a)) for a in A},
    recovered_errors=sum(x['recovered_data_error'] for x in flat),raw_errors=[r for r in events if r['status'] in errors],
    markets=markets,new_orders=[compact(r) for r in new],latest=latest,closest=closest,
    z_formula_check=quant(formula),scan_completion_ms=quant([1000*(x['record'].get('scan_completed_ts',x['record']['_saved_ts'])-x['ts']) for x in flat]),
    health={k:v for k,v in status.items() if k not in ('trades','scans')},heartbeat_age=time.time()-status['ts'],
    ntp=subprocess.check_output(['timedatectl','show','-p','NTPSynchronized','--value'],text=True).strip(),
    current_scans=[r for r in status.get('scans',[]) if r.get('asset') in A],preservation=before,
    source_flags_services_unchanged=True)
for name,data in [('audit.json',out),('events.json',events),('orders.json',orders),('attempts.json',attempts),('classified.json',flat)]:
    (P/name).write_text(json.dumps(data,indent=2)+'\n')
print(json.dumps({k:v for k,v in out.items() if k not in ('health','preservation','closest','latest','current_scans')},indent=2))
print(json.dumps(dict(health={k:out['health'].get(k) for k in ('mode','armed','halted','error','stopped','contracts','max_contracts','max_risk','daily_loss_budget','account')},latest={a:{k:v for k,v in r.items() if k!='signal'} for a,r in latest.items()}),indent=2))
