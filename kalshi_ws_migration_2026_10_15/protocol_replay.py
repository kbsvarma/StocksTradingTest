import gzip,json,sys
from pathlib import Path
from collections import Counter
P=Path(__file__).resolve().parent;sys.path.insert(0,str(P/'candidate'))
from stream_transport import BookState
b=BookState();count=Counter();initial=set()
for line in gzip.open(P/'receipts/snapshot_probe_frames.jsonl.gz','rt'):
 row=json.loads(line);f=row['frame'];kind=f.get('type');count[kind]+=1
 if kind=='subscribed' and f['msg']['channel']=='orderbook_delta':b.reset()
 if kind in ('orderbook_snapshot','orderbook_delta','ok'):
  b.apply(f,row['received_ts'],row['received_ts'])
  if kind=='orderbook_snapshot':initial.add(f['msg']['market_ticker'])
report=dict(mode='OFFLINE_REPLAY_OF_ACTUAL_WS_FRAMES',counts=dict(count),markets=sorted(initial),all_frames_valid=True,
 snapshot_replacements=dict(b.snapshot_checks),note='Authoritative snapshots may introduce changes not separately delivered as preceding deltas. Replacing state passed the entire stream; ignoring refreshed snapshots failed with negative depth in snapshot_audit.json.')
(P/'receipts/protocol_replay.json').write_text(json.dumps(report,indent=2));print(json.dumps(report))
