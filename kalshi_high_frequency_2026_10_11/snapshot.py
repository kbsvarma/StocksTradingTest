"""Consistent SQLite backup of the public-only observer; no observer changes."""
from pathlib import Path
import sqlite3, json, hashlib, time
P=Path(__file__).resolve().parent
src=P.parent/'kalshi_every_window_2026_10_03/paper_state/paper.sqlite3'
dest=P/'forward_snapshot.sqlite3'
assert not dest.exists(), 'Preserve first snapshot'
c=sqlite3.connect('file:'+str(src)+'?mode=ro',uri=True)
d=sqlite3.connect(dest); c.backup(d); d.close(); c.close()
(P/'snapshot_receipt.json').write_text(json.dumps(dict(source=str(src),snapshot_ts=time.time(),sha256=hashlib.sha256(dest.read_bytes()).hexdigest(),size=dest.stat().st_size),indent=2))
print((P/'snapshot_receipt.json').read_text())
