"""Read-only broker audit; optionally exercise recovery against in-memory state."""
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import yaml
from ib_insync import IB

base = Path('/home/varma/trendbot/trend_bot')
source = Path(sys.argv[1]) if len(sys.argv) > 1 else base
sys.path.insert(0, str(source))
from bot import TrendBot
from executor import IBKRPaperExecutor
from state_db import StateDB

cfg = yaml.safe_load((base / 'config.yaml').read_text())
assert cfg['executor']['venue'] == 'ibkr_paper'
db = StateDB.__new__(StateDB)
db.con = sqlite3.connect(f'file:{base}/state/trendbot.db?mode=ro', uri=True)
db.account, db.venue = db.con.execute('SELECT account,venue FROM runtime_identity').fetchone()
assert db.venue == 'ibkr_paper' and db.account.startswith('D')
ib = IB()
try:
    ib.connect('127.0.0.1', cfg['ibkr']['paper_port'], clientId=179106,
               readonly=True, timeout=15)
    assert db.account in ib.managedAccounts()
    assert all(a.startswith('D') for a in ib.managedAccounts())
    ib.reqPositions()
    orders = ib.reqAllOpenOrders()
    ib.sleep(1)
    ex = IBKRPaperExecutor.__new__(IBKRPaperExecutor)
    ex.ib, ex.account = ib, db.account
    ex._stop_trades = {t.order.orderRef.split(':')[1]: t for t in orders
                      if t.order.orderRef.startswith('trendbot:')
                      and t.order.orderRef.endswith(':stop')}
    b = TrendBot.__new__(TrendBot)
    state = json.loads((base / 'state/state.json').read_text())
    before_halt = {k: state.get(k) for k in ('halt_entries', 'halt_reason', 'last_wake', 'last_wake_status')}
    b.st = SimpleNamespace(state=state, dir=base / 'state',
                          pos=lambda s: state['positions'][s],
                          save=lambda: None, event=lambda msg: None)
    b.all_instruments = {**cfg['core_instruments'], **cfg['sleeve_instruments'],
                         **cfg.get('satellite_instruments', {})}
    b.ex, b.db = ex, db
    b._degraded, b._startup_done, b._reconcile_ok = set(), True, True
    b.dlog = SimpleNamespace(log=lambda *a, **kw: None)
    result = {
        'checked_at': datetime.now(timezone.utc).isoformat(),
        'paper_accounts_only': True, 'persisted_state': before_halt,
        'position_diff': b._position_diff(), 'protection': b._protection_readiness(),
        'pending_events': len(db.pending_events()),
        'unapplied_executions': db.unapplied_execution_count(),
        'readiness': db.readiness(),
        'positions': [dict(con_id=p.contract.conId, symbol=p.contract.localSymbol,
                           qty=p.position) for p in ib.positions() if p.position],
        'orders': [dict(order_id=t.order.orderId, client_id=t.order.clientId,
                       symbol=t.contract.localSymbol, con_id=t.contract.conId,
                       ref=t.order.orderRef, qty=t.order.totalQuantity,
                       filled=t.orderStatus.filled, action=t.order.action,
                       type=t.order.orderType, stop=t.order.auxPrice,
                       status=t.orderStatus.status, tif=t.order.tif) for t in orders],
        'would_clear_in_memory': b._recover_protection_halt(),
    }
    print(json.dumps(result, indent=2))
finally:
    ib.disconnect()
    db.con.close()
