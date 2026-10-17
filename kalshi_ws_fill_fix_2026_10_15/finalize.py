"""Finalize only after independently witnessed actual NO WS confirmation."""
import importlib.util,json,subprocess,time
from pathlib import Path
P=Path(__file__).resolve().parent;L=P.parent/'kalshi_revalidation_2026_09_12/live_v1';S=L/'state'
spec=importlib.util.spec_from_file_location('release_helpers',P.parent/'kalshi_usdcad_2026_10_14/activate.py')
a=importlib.util.module_from_spec(spec);spec.loader.exec_module(a)
d=json.loads((P/'receipts/deployment_receipt.json').read_text())
v=json.loads((P/'receipts/actual_verification.json').read_text())
assert v['status']=='ACTUAL_NO_WEBSOCKET_FILL_VERIFIED'
assert any(x['side']=='no' and x['actual_legacy_actions']==['sell'] and x['initial_fill_transport']=='websocket' for x in v['proven'])
a.validate_hashes(d['source_hashes'])
pre=json.loads((P/'receipts/preflight.json').read_text())
assert a.flags()==pre['flags']
s=json.loads((S/'status.json').read_text());assert a.configs(s)==d['config']
assert time.time()-s['ts']<15 and s['armed'] and not any(s[k] for k in ('error','halted','stopped'))
w=s['market_transport'];assert w['connected'] and w['fill_validation_version']==2
assert w['fill_audit']['dropped']==w['fill_audit']['errors']==0
for table,key,file in [('orders','ticker','orders_before.json'),('order_attempts','client_id','attempts_before.json')]:
    now=a.records(table,key);before=json.loads((P/'receipts'/file).read_text())
    assert all(now.get(k)==r for k,r in before.items()),'Existing financial records changed'
other=['kalshi-dashboard.service','kalshi-execution-observer.service','kalshi-tennis90-paper.service','kalshi-weather-early-paper.service']
services={n:a.service(n) for n in list(d['services_after'])}
assert all(services[n]==d['services_before'][n] for n in other)
assert services['kalshi-crypto-z2-live.service']['MainPID']==d['services_after']['kalshi-crypto-z2-live.service']['MainPID']
paper=P.parent/'kalshi_resting_paper_2026_10_14'
assert a.sha(paper/'observer.py')==pre['paper_source_hash']
assert a.sha(paper/'frozen_config.json')==pre['paper_config_hash']
ps=json.loads((paper/'status.json').read_text());assert time.time()-ps['ts']<15 and not ps['health']
subprocess.run(['systemctl','--user','stop','kalshi-ws-fill-witness-20261015.service'],check=True)
final=dict(ts=time.time(),actual_ws_fills_verified=True,verified_trades=len(v['proven']),
    config_unchanged=True,source_matches_deployment=True,flags_unchanged=True,prior_financial_records_preserved=True,
    unrelated_services_unchanged=True,temporary_witness_stopped=True,services=services,stream=w,
    verification_file='actual_verification.json',paper_healthy=True)
a.save(P/'receipts/final_verification.json',final)
d['actual_ws_fills_verified']=True;d['actual_ws_verification_ts']=final['ts'];d['verification_file']=str(P/'receipts/actual_verification.json')
a.save(P/'receipts/deployment_receipt.json',d)
print(json.dumps({k:v for k,v in final.items() if k not in ('services','stream')},indent=2))
