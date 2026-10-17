"""Recompute recorded rejections from pre-change settings, without orders/network."""
from pathlib import Path
import gzip,json,math,statistics
from collections import Counter,defaultdict
from datetime import datetime
from zoneinfo import ZoneInfo
P=Path(__file__).resolve().parent
release=P.parent/'kalshi_crypto_frequency_2026_10_09'
start=json.loads((release/'deployment_started.json').read_text())['started_ts']
before=json.loads((release/'deployment_receipt.json').read_text())['config']
with gzip.open(P/'events.json.gz','rt') as f:events=json.load(f)
post=[e for e in events if e['saved_ts']>=start]
checks=[];cushions=defaultdict(list)
for e in post:
 rule=before['assets'][e['asset']]['rules'][e['lane']];status=e['status'];rem=e.get('remaining_minutes');ask=e.get('signal_ask');bid=e.get('signal_bid')
 if status=='no_current_market':
  assert e['decision_ts']%900==0,'Missing market outside quarter-hour rollover'
  checks.append('quarter_hour_rollover');continue
 in_time=rule['min_minutes']<=rem<=rule['max_minutes']
 if status=='outside_window':
  assert not in_time;checks.append('outside_window');continue
 in_price=float(rule['signal_floor'])-1e-8<=ask<float(rule['signal_ceiling'])-1e-8 and ask-bid<=.02+1e-8
 if status=='price_gate':
  assert in_time and not in_price;checks.append('price_gate');continue
 assert status=='cushion_gate',e
 z=(1 if e['side']=='yes' else -1)*(e['spot']/e['strike_proxy']-1)/(e['sigma']*math.sqrt(rem))
 threshold=max(.5,rule['z_base_at_5m']+rule['z_slope']*math.log2(rem/5))
 assert in_time and in_price and z<threshold
 assert abs(z-e['z'])<1e-12 and abs(threshold-e['required_z'])<1e-12
 checks.append('cushion_gate')
 cushions[(e['asset'],e['lane'])].append(dict(ticker=e['ticker'],z=z,threshold=threshold,minutes=rem,ask=ask,decision_ts=e['decision_ts']))
latencies=[e['saved_ts']-e['decision_ts'] for e in post]
rows=json.loads((P/'orders.json').read_text());last=max((r for r in rows if float(r.get('filled',0))>0),key=lambda r:r['created_ts'])
ET=ZoneInfo('America/New_York')
result=dict(checked=len(checks),rejections_reproduced=dict(Counter(checks)),gate_mismatches=0,qualifying_signals=0,
 caveat='Recomputed gates on recorded inputs; not a new reconstruction of historical source candles, volatility bars or every unobserved tick.',
 highest_z_by_asset_lane={a+'/'+lane:max(v,key=lambda x:x['z']) for (a,lane),v in cushions.items()},
 decision_record_age_seconds=dict(median=statistics.median(latencies),max=max(latencies)),
 last_filled_entry_et=datetime.fromtimestamp(last['created_ts'],ET).isoformat(),last_filled_ticker=last['ticker'],quiet_minutes_already_elapsed_before_restart=(start-last['created_ts'])/60)
(P/'decision_verification.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps(result,indent=2))
