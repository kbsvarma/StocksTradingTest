"""Isolated read-only USD/CAD loss audit; public/authenticated GETs only."""
import datetime as dt
import hashlib
import json
import pathlib
import sqlite3
import subprocess
import sys
import time

ROOT = pathlib.Path('/home/varma/kalshi_revalidation_2026_09_12/live_v1')
OUT = pathlib.Path(__file__).resolve().parent
TICKER = 'KXUSDCAD15M-26OCT142000-30'
sys.path.insert(0, str(ROOT))
from kalshi_client import Credentials, KalshiClientV2

def save(name, data):
    (OUT/name).write_text(json.dumps(data, indent=2)+'\n')

def preservation():
    return dict(ts=time.time(), hashes={p.name:hashlib.sha256(p.read_bytes()).hexdigest()
        for p in ROOT.iterdir() if p.suffix in ('.py','.css')},
        flags={p.name:hashlib.sha256(p.read_bytes()).hexdigest()
               for p in (ROOT/'state').iterdir() if p.name.startswith(('ARM','HALT'))},
        services=subprocess.check_output(['systemctl','--user','show',
            'kalshi-crypto-z2-live.service','kalshi-dashboard.service',
            '-p','Id','-p','ActiveState','-p','MainPID','-p','NRestarts'],text=True))

save('preservation_before.json',preservation())
db=sqlite3.connect((ROOT/'state/orders.sqlite3').as_uri()+'?mode=ro',uri=True)
db.execute('PRAGMA query_only=ON');db.execute('BEGIN')
order=json.loads(db.execute('SELECT record FROM orders WHERE ticker=?',(TICKER,)).fetchone()[0])
save('order.json',order)
save('attempts.json',[json.loads(r[0]) for r in db.execute('SELECT record FROM order_attempts WHERE ticker=?',(TICKER,))])
save('events.json',[dict(id=i,ts=t,event=json.loads(e)) for i,t,e in db.execute(
    'SELECT id,ts,event FROM events WHERE ts BETWEEN ? AND ? AND event LIKE ? ORDER BY id',
    (order['close_ts']-1000,order['close_ts']+120,'%'+TICKER+'%'))])
save('portfolio_at_audit.json',[json.loads(r[0]) for r in db.execute('SELECT record FROM orders')])
db.rollback();db.close()
save('status_at_audit.json',json.loads((ROOT/'state/status.json').read_text()))
(OUT/'source').mkdir(exist_ok=True)
for f in ('lanes.py','pyth_data.py','market_data.py','engine.py','price_rules.py','sizing.py'):
    (OUT/'source'/f).write_bytes((ROOT/f).read_bytes())

client=KalshiClientV2(Credentials.load(),dry_run=True,timeout=5)
def get(name,path,params=None,signed=False):
    time.sleep(1.5)
    start=time.time()
    response=client._request('GET',path,params=params,signed=signed)
    save(name,dict(request_path=path,params=params,started=start,received=time.time(),response=response))
    return response

m=get('market.json','/markets/'+TICKER)['market']
get('order_exchange.json','/portfolio/orders/'+order['order_id'],signed=True)
get('fills_exchange.json','/portfolio/fills',dict(order_id=order['order_id'],subaccount=0,limit=100),True)
get('settlement_exchange.json','/portfolio/settlements',dict(ticker=TICKER,subaccount=0,limit=100),True)
get('chart.json','/live_data/events/'+m['event_ticker'],dict(range='1h'))
get('candles.json','/series/KXUSDCAD15M/markets/'+TICKER+'/candlesticks',
    dict(start_ts=order['close_ts']-900,end_ts=order['close_ts'],period_interval=1))
old=get('prior_markets.json','/markets',dict(series_ticker='KXUSDCAD15M',status='settled',
    max_close_ts=order['close_ts']-1,limit=2))['markets']
if old:
    get('prior_chart.json','/live_data/events/'+old[0]['event_ticker'],dict(range='1h'))
save('preservation_after.json',preservation())
print(json.dumps(dict(collected_at=dt.datetime.now(dt.timezone.utc).isoformat(),ticker=TICKER,
    result=m.get('result'),strike=m.get('floor_strike'),expiration=m.get('expiration_value'),
    rules_primary=m.get('rules_primary'),rules_secondary=m.get('rules_secondary'))))
