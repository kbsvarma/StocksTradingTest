"""Analyze a consistent captured ledger. No network, credentials or live writes."""
from pathlib import Path
from collections import Counter, defaultdict
from datetime import datetime
from zoneinfo import ZoneInfo
from decimal import Decimal as D
import json, sqlite3, statistics, sys

P=Path(__file__).resolve().parent
sys.path.insert(0,str(P/'source'))
from engine import limit_price
from lanes import lane_for
ET=ZoneInfo('America/New_York')
def et(t):return datetime.fromtimestamp(t,ET).isoformat()
def quantiles(values):
    a=sorted(values)
    return dict(n=len(a),median=statistics.median(a),p95=a[int((len(a)-1)*.95)],max=max(a)) if a else {}
db=sqlite3.connect((P/'snapshot.sqlite3').as_uri()+'?mode=ro',uri=True)
start=json.loads((P/'z3_receipt.json').read_text())['verified_ts']
start_min=int(start//60)*60
end=json.loads((P/'capture.json').read_text())['captured_ts']
upgrade=json.loads((P/'upgrade_receipt.json').read_text())['verified_ts']
rows=[json.loads(r[0]) for r in db.execute('select record from orders order by rowid')]
orders={r['ticker']:r for r in rows}
events=[dict(json.loads(e),event_ts=ts) for ts,e in db.execute('select ts,event from events where ts>=? order by id',(start_min,))]
groups=defaultdict(list)
for e in events:groups[(e['lane'],e['asset'],e['decision_ts'])].append(e)
observer=[json.loads(l) for l in (P/'observer.jsonl').read_text().splitlines()]

def book_check(levels,s):
    cap=limit_price(s)[1];floor=lane_for(s).floor;need=D(5);cost=D(0)
    for price,qty in sorted((1-D(str(p)),D(str(n))) for p,n in levels):
        if qty<=0:continue
        if price<floor:return dict(pass_book=False,reason='below_floor',cap=str(cap))
        if price>cap:break
        take=min(need,qty);cost+=take*price;need-=take
        if not need:return dict(pass_book=True,average=str(cost/5),cap=str(cap))
    return dict(pass_book=False,reason='insufficient_depth',cap=str(cap))

out=dict(start_et=et(start),end_et=et(end),elapsed_hours=(end-start)/3600,lanes={})
for lane in ('cushion_z2','premium_z3'):
    own=[v for k,v in groups.items() if k[0]==lane]
    first=[v[0] for v in own]
    q=[v for v in own if v[0].get('z',-999)>=lane_for(v[0]).z]
    live_orders=[r for r in rows if r.get('lane','cushion_z2')==lane and r['created_ts']>=start]
    settled=[r for r in live_orders if r['status']=='settled']
    traces=[]
    for t in sorted({v[0]['ticker'] for v in q}):
        ownq=[v for v in q if v[0]['ticker']==t]
        r=orders.get(t,{})
        details=[]
        for v in ownq:
            s=v[0];T=s['decision_ts'];levels=[(x['observed_ts'],x['levels']) for x in observer if x.get('ticker')==t and x.get('decision_ts')==T and x.get('levels')]
            checked=[dict(ts=ts,age=ts-T,after_last_scan=ts>v[-1]['event_ts'],**book_check(b,s)) for ts,b in levels]
            details.append(dict(decision_ts=T,decision_et=et(T),remaining=s['remaining_minutes'],side=s['side'],z=s['z'],ask=s['signal_ask'],checks=len(v),first_age=v[0]['event_ts']-T,last_age=v[-1]['event_ts']-T,
                last_status=v[-1]['status'],executions=dict(Counter(x.get('execution_status','none') for x in v)),
                observer_checks=checked,observer_opportunities_after_last_within_25=[x for x in checked if x['pass_book'] and x['after_last_scan'] and 0<=x['age']<=25]))
        traces.append(dict(ticker=t,ledger_lane=r.get('lane'),ledger_status=r.get('status'),pnl=r.get('pnl'),filled=r.get('filled'),details=details))
    expected=[(lane,a,T) for T in range(start_min,int(end//60)*60+1,60) for a in ('BTC','ETH','SOL','XRP') if T+30<end]
    missing=[dict(lane=k[0],asset=k[1],decision_ts=k[2],et=et(k[2])) for k in expected if k not in groups]
    out['lanes'][lane]=dict(unique_asset_minutes=len(first),first_statuses=dict(Counter(x['status'] for x in first)),
      qualified_minutes=len(q),qualified_markets=len(traces),qualifying_traces=traces,missing_scans=missing,
      first_scan_age=quantiles([x['event_ts']-x['decision_ts'] for x in first]),
      qualified_first_age=quantiles([v[0]['event_ts']-v[0]['decision_ts'] for v in q]),
      qualified_last_age=quantiles([v[-1]['event_ts']-v[-1]['decision_ts'] for v in q]),
      actual=dict(markets=len(live_orders),filled=sum(D(r.get('filled','0'))>0 for r in live_orders),settled=len(settled),wins=sum(r['side']==r['result'] for r in settled),losses=sum(r['side']!=r['result'] for r in settled),net=str(sum((D(r['pnl']) for r in settled),D(0)))),
      errors=[x for v in own for x in v if x.get('status')=='data_error'])
    out['lanes'][lane]['latency_by_asset']={a:quantiles([x['event_ts']-x['decision_ts'] for x in first if x['asset']==a]) for a in ('BTC','ETH','SOL','XRP')}
    out['lanes'][lane]['in_window_first_statuses']=dict(Counter(x['status'] for x in first if 3<=x.get('remaining_minutes',-1)<=10))

out['post_upgrade']=dict(start_et=et(upgrade),hours=(end-upgrade)/3600,
    orders=[{k:r.get(k) for k in ('ticker','lane','created_ts','status','filled','attempt_number','pnl')} for r in rows if r['created_ts']>=upgrade],
    event_statuses=dict(Counter(x['status'] for x in events if x['event_ts']>=upgrade)),
    executions=dict(Counter(x.get('execution_status') for x in events if x['event_ts']>=upgrade and x.get('execution_status'))))
out['daily']={}
for day in sorted({et(r['created_ts'])[:10] for r in rows if r['created_ts']>=start}):
    out['daily'][day]={lane:dict(markets=sum(et(r['created_ts'])[:10]==day and r.get('lane','cushion_z2')==lane for r in rows if r['created_ts']>=start),filled=sum(et(r['created_ts'])[:10]==day and r.get('lane','cushion_z2')==lane and D(r.get('filled','0'))>0 for r in rows if r['created_ts']>=start)) for lane in ('cushion_z2','premium_z3')}

# Why each daily risk denial occurred: actual entry records, first known settlement
# evidence is bounded by logs; close+30 provides an optimistic release bound.
blocked=[]
for e in events:
    if e.get('execution_status')!='Daily loss budget including open risk':continue
    T=e['event_ts'];day=et(T)[:10]
    open_min=[r for r in rows if r['created_ts']<=T<r['close_ts']+30 and D(r.get('filled','0'))>0]
    settled_before=[r for r in rows if r.get('settled_day')==day and r.get('pnl') is not None and r['close_ts']+30<T]
    realized=sum((D(r['pnl']) for r in settled_before),D(0))
    reserve=sum((D(r['requested_count'])*lane_for(r).reserve for r in open_min),D(0))
    blocked.append(dict(ticker=e['ticker'],lane=e['lane'],ts=T,et=et(T),realized_upper_bound=str(realized),open_reserve_min=str(reserve),new_reserve=str(5*lane_for(e).reserve),open_markets=[r['ticker'] for r in open_min],worst_case=str(realized-reserve-5*lane_for(e).reserve)))
out['daily_risk_denials']=blocked
out['observer']=dict(rows=len(observer),errors=[r for r in observer if r.get('error')],lane_tag_rows=sum('lane' in r for r in observer))
(P/'event_analysis.json').write_text(json.dumps(out,indent=2)+'\n')
compact={k:v for k,v in out.items() if k not in ('lanes','observer')}
compact['lanes']={k:{kk:vv for kk,vv in v.items() if kk!='qualifying_traces'} for k,v in out['lanes'].items()}
print(json.dumps(compact,indent=2))
