"""Read only local production receipts; no network or financial client imports."""
import collections,hashlib,json,sqlite3,statistics,subprocess,time
from pathlib import Path
P=Path(__file__).resolve().parent;L=P.parent/'kalshi_revalidation_2026_09_12/live_v1'
now=time.time();start=1791998880;probe_start=1791999275.58;probe_end=json.loads((P/'collector_status.json').read_text()).get('stopped_ts',1791999500)
db=sqlite3.connect('file:'+str(L/'state/orders.sqlite3')+'?mode=ro',uri=True)
events=[]
for ts,raw in db.execute('select ts,event from events where ts>=? order by id',(now-86400,)):
 r=json.loads(raw);r['_saved_ts']=ts;events.append(r)
orders=[json.loads(raw) for raw, in db.execute('select record from orders')]
post=[r for r in events if r['_saved_ts']>=start];cad=[r for r in post if r.get('asset')=='USDCAD']
(P/'events_final.json').write_text(json.dumps(post))
groups=collections.defaultdict(list)
for r in events:
 if r.get('decision_ts') is not None:groups[(r.get('asset'),r.get('lane'),r['decision_ts'])].append(r)
errs=[r for r in events if '429' in str(r.get('error','')) or (r.get('status')=='data_error' and '429' in str(r))]
recovered=[];unresolved=[]
for key,rs in groups.items():
 if not any(r in errs for r in rs):continue
 first=next(r for r in rs if r in errs)
 successful=[r for r in rs if r['_saved_ts']>first['_saved_ts'] and r.get('status') not in ('data_error','incomplete_spot','decision_candle_unavailable','no_decision_quote')]
 item=dict(asset=key[0],lane=key[1],decision_ts=key[2],first_error_age=first['_saved_ts']-key[2],errors=sum(r in errs for r in rs))
 if successful:
  last=successful[0];item.update(recovery_age=last['_saved_ts']-key[2],recovery_status=last.get('status'),qualified=any(r.get('signal_qualified') for r in successful));recovered.append(item)
 else:unresolved.append(item)
firsts={}
for r in cad:firsts.setdefault((r.get('lane'),r.get('decision_ts')),r)
def times(rows):
 v=sorted(r['_saved_ts']-r['decision_ts'] for r in rows if r.get('decision_ts'))
 return dict(n=len(v),min=min(v) if v else None,median=statistics.median(v) if v else None,p95=v[min(len(v)-1,int(.95*len(v)))] if v else None,max=max(v) if v else None)
s=json.loads((L/'state/status.json').read_text())
qsignals={ (r.get('ticker'),r.get('lane'),r.get('decision_ts')) for r in cad if r.get('signal_qualified')}
h=json.loads((L/'state/dashboard_quote_status.json').read_text())
report=dict(ts=now,window_start=start,heartbeat_age=now-s['ts'],mode=s['mode'],halted=s['halted'],error=s['error'],ntp=subprocess.check_output(['timedatectl','show','-p','NTPSynchronized','--value'],text=True).strip(),
 cad=dict(scan_events=len(cad),decision_minutes=len({r['decision_ts'] for r in cad}),status_counts=dict(collections.Counter(r.get('status') for r in cad)),first_scan_latency={lane:times(r for (l,t),r in firsts.items() if l==lane) for lane in ('cushion_z2','premium_z3')},qualified_signals=len(qsignals),qualified_markets=sorted({k[0] for k in qsignals}),executable_events=sum(bool(r.get('execution_executable')) for r in cad),orders=[r for r in orders if r.get('asset')=='USDCAD'],current_settings=s['assets']['USDCAD']),
 rate_limits=dict(events=len(errs),by_asset=dict(collections.Counter(r.get('asset') for r in errs)),before_probe=sum(r['_saved_ts']<probe_start for r in errs),during_probe=sum(probe_start<=r['_saved_ts']<=probe_end for r in errs),after_probe=sum(r['_saved_ts']>probe_end for r in errs),affected_lane_minutes=len(recovered)+len(unresolved),recovered=len(recovered),unresolved=unresolved,recovery_max_seconds=max((r['recovery_age'] for r in recovered),default=None),qualified_on_recovery=sum(r['qualified'] for r in recovered)),
 quotes=dict(health_age=now-h['ts'],requests=len(h['requests']),errors=sum(bool(r['error']) for r in h['requests']),states={a:dict(age=now-v['checked_ts'],error=v['error']) for a,v in h['states'].items()}),
 preserved=dict(contracts=s['contracts'],max_contracts=s['max_contracts'],max_risk=s['max_risk'],daily_loss_budget=s['daily_loss_budget']))
(P/'rate_limit_recovery.json').write_text(json.dumps(dict(recovered=recovered,unresolved=unresolved),indent=2))
(P/'passive_audit.json').write_text(json.dumps(report,indent=2))
(P/'quote_health_final.json').write_text(json.dumps(h,indent=2))
print(json.dumps(report,indent=2))
