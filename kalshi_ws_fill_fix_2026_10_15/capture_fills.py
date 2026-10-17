"""Independent receive-only fill witness. Never creates an exchange/order client."""
import json
import os
from pathlib import Path
import signal
import sys
import time

P=Path(__file__).resolve().parent
sys.path.insert(0,str(P/'source_before'))
from stream_transport import make_stream
from kalshi_client import Credentials

stop=False
def stopped(*args):
    global stop
    stop=True
signal.signal(signal.SIGTERM,stopped)
signal.signal(signal.SIGINT,stopped)
received=0
def capture(frame,wall):
    global received
    if frame.get('type') not in ('fill','subscribed','error'):return
    row=dict(received_ts=wall,frame=frame)
    with (P/'receipts/independent_fill_frames.jsonl').open('a') as f:
        f.write(json.dumps(row)+'\n');f.flush();os.fsync(f.fileno())
    if frame.get('type')=='fill':received+=1

started=time.time()
ws=make_stream(Credentials.load(),on_frame=capture)
try:
    while not stop and time.time()-started<14400:
        status=dict(ts=time.time(),started=started,mode='RECEIVE_ONLY_NO_ORDER_CLIENT',
                    received_fills=received,stream=ws.status())
        tmp=P/'receipts/independent_status.tmp'
        tmp.write_text(json.dumps(status,indent=2));tmp.replace(P/'receipts/independent_status.json')
        time.sleep(2)
finally:
    ws.close()
