import sys,json,time,gzip
from pathlib import Path
from collections import deque
P=Path(__file__).resolve().parent;sys.path.insert(0,str(P/'candidate'))
import stream_transport as st
from kalshi_client import Credentials
st.SNAPSHOT_REFRESH_SECONDS=1.0
raw=gzip.open(P/'receipts/snapshot_probe_frames.jsonl.gz','wt')
def capture(r,wall):raw.write(json.dumps(dict(frame=r,received_ts=wall))+'\n')
ws=st.make_stream(Credentials.load(),on_frame=capture)
r=json.loads((P/'receipts/shadow_final.json').read_text())
for t in r['ws']['targets']:ws.watch(t,1792043100)
start=time.time()
try:
 while time.time()-start<120:
    time.sleep(2)
    (P/'receipts/snapshot_probe.json').write_text(json.dumps(ws.status(),indent=2))
finally:ws.close();raw.close()
