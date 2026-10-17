"""Read-only broker/state reconciliation; never submits or cancels orders."""
from ib_insync import IB
from pathlib import Path
import json,time
ib=IB()
try:
 ib.connect('127.0.0.1',4002,clientId=179039,readonly=True,timeout=10)
 positions=ib.positions();orders=ib.reqAllOpenOrders()
 state=json.loads(Path('/home/varma/trendbot/trend_bot/state/state.json').read_text())
 local=[s for s,p in state.get('positions',{}).items() if p.get('qty') or p.get('position')]
 if positions or orders or local:raise SystemExit('Non-flat broker or migrated state; reconcile before activation.')
 print('PASS: broker and migrated Trend state are flat.')
finally:ib.disconnect()
