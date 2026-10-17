"""Run remotely: export only this bot's ledger, scan diagnostics and code hashes."""
from pathlib import Path
import sqlite3,json,hashlib,time,collections
p=Path('/home/varma/kalshi_revalidation_2026_09_12/live_v1')
c=sqlite3.connect((p/'state/orders.sqlite3').as_uri()+'?mode=ro',uri=True)
orders=[json.loads(r[0]) for r in c.execute('select record from orders')]
cutoff=time.time()-7*86400;mins=collections.defaultdict(set);latency=collections.defaultdict(list)
for ts,record in c.execute('select ts,event from events where ts>=?',(cutoff,)):
    r=json.loads(record);a=r.get('asset','?');st=r.get('status','?')
    if r.get('ticker'):mins[(a,st)].add((r.get('ticker'),r.get('decision_ts')))
    if st in ('eligible','paper_fill'):
        latency[a].append({k:r.get(k) for k in ['ticker','decision_ts','observed_ts','book_observed_ts','execution_status','lane']})
c.close()
status=json.loads((p/'state/status.json').read_text());status.pop('trades',None)
print(json.dumps(dict(snapshot_ts=time.time(),production_hashes={n:hashlib.sha256((p/n).read_bytes()).hexdigest() for n in ['engine.py','runner.py','market_data.py','dashboard_panel.py','sizing.py','execution_retry.py','kalshi_client.py','lanes.py']},status=status,orders=orders,last7_days_scanner={a:{st:len(v) for (aa,st),v in mins.items() if aa==a} for a in ['BTC','ETH','SOL','XRP']},eligible_latency=latency)))
