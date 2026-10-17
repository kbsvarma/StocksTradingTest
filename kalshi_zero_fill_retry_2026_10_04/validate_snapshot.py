"""Read-only live checks plus migration of an isolated ledger fixture. No POST."""
import hashlib,json,sqlite3,sys,time
from pathlib import Path

P=Path(__file__).resolve().parent
C=P/'candidate'
LIVE=Path('/home/varma/kalshi_revalidation_2026_09_12/live_v1')
sys.path.insert(0,str(C))
from engine import Store,Engine,Exchange
from kalshi_client import Credentials,KalshiClientV2
from dashboard_panel import ledger_summary
from activate import verify_hashes,live_readiness

def main():
    original=json.loads((C/'original_hashes.json').read_text())
    verify_hashes(LIVE,original)
    for name in ('market_data.py','sizing.py','lanes.py','kalshi_client.py'):
        assert hashlib.sha256((C/name).read_bytes()).hexdigest()==original[name],name
    source=sqlite3.connect((LIVE/'state/orders.sqlite3').as_uri()+'?mode=ro',uri=True)
    rows=source.execute('SELECT ticker,record FROM orders ORDER BY rowid').fetchall();source.close()
    scratch=P/'validation';scratch.mkdir(exist_ok=True)
    db=scratch/('migration_'+str(time.time_ns())+'.sqlite3')
    dest=sqlite3.connect(db)
    dest.execute('CREATE TABLE orders (ticker TEXT PRIMARY KEY,record TEXT NOT NULL)')
    dest.executemany('INSERT INTO orders VALUES (?,?)',rows);dest.commit();dest.close()
    store=Store(db)
    after=store.db.execute('SELECT ticker,record FROM orders ORDER BY rowid').fetchall()
    assert after==rows
    before_summary=ledger_summary([json.loads(r) for _,r in rows])
    after_summary=ledger_summary(store.all());assert before_summary==after_summary
    assert store.db.execute('SELECT count(*) FROM order_attempts').fetchone()[0]==sum(bool(json.loads(r).get('client_id')) for _,r in rows)
    # Validate the new zero-confirmation GET protocol on the exact historical order.
    target=store.get('KXETH15M-26OCT041830-30');target['attempt_number']=1
    client=KalshiClientV2(Credentials.load(),dry_run=True,timeout=5)
    Engine(Exchange(client),store,scratch,live=False).confirm_zero_retry(target)
    readiness=live_readiness()
    result=dict(status='PASS',checked_ts=time.time(),legacy_rows_preserved=len(rows),
                additive_migration=True,summary_unchanged=True,
                actual_zero_order_get_confirmation=True,unchanged_scanner_sizing_lanes_transport=True,
                readiness=readiness,summary={k:str(v) for k,v in before_summary.items()})
    (P/'snapshot_validation.json').write_text(json.dumps(result,indent=2)+'\n')
    store.db.close();print(json.dumps(result))

if __name__=='__main__':main()
