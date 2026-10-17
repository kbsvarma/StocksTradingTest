import json
import sqlite3
from pathlib import Path

base = Path('/home/varma/trendbot/trend_bot')
c = sqlite3.connect(f'file:{base}/state/trendbot.db?mode=ro', uri=True)
account, venue = c.execute('SELECT account,venue FROM runtime_identity').fetchone()
pending = c.execute("SELECT sym,event_type,status FROM broker_events WHERE account=? AND venue=? AND status NOT IN ('COMMITTED','RESOLVED','CANCELLED','FLAT')", (account, venue)).fetchall()
unapplied = c.execute('SELECT e.sym,e.side,e.qty,e.exec_time FROM executions e LEFT JOIN applied_executions a ON a.account=e.account AND a.exec_id=e.exec_id WHERE e.account=? AND ((e.exec_id IS NOT NULL AND a.exec_id IS NULL) OR (e.exec_id IS NULL AND e.transition_key IS NOT NULL))', (account,)).fetchall()
print(json.dumps({'venue': venue, 'pending_events': pending, 'unapplied_executions': unapplied}, indent=2))
