"""Public GET-only independent reconstruction; no live bot imports or changes."""
import concurrent.futures,datetime as dt,json,math,statistics,time
from pathlib import Path
from zoneinfo import ZoneInfo
import requests

P=Path(__file__).resolve().parent;ET=ZoneInfo('America/New_York')
flat=json.loads((P/'classified.json').read_text())
start=dt.datetime(2026,10,12,tzinfo=ET).timestamp()
rows=[x for x in flat if start<=x['T'] and x['category'] not in ('outside_window','no_current_market','already_owned')]
def fetch_coinbase(asset):
    selected=[x for x in rows if x['asset']==asset]
    first=int(min(x['T'] for x in selected)-3660);last=int(max(x['T'] for x in selected))
    bars={};receipts=[]
    with requests.Session() as session:
        for begin in range(first,last,290*60):
            end=min(last,begin+290*60)
            url=f'https://api.exchange.coinbase.com/products/{asset}-USD/candles'
            time.sleep(1.25)
            began=time.time()
            for retry in range(3):
                r=session.get(url,params=dict(granularity=60,start=begin,end=end),timeout=8)
                if r.status_code!=429:break
                time.sleep(3*(retry+1))
            r.raise_for_status();raw=r.json();receipts.append(dict(url=r.url,received=time.time(),elapsed=time.time()-began,data=raw))
            bars.update({int(x[0]):float(x[4]) for x in raw})
    return asset,bars,receipts
allbars={};allreceipts={}
with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
    for asset,bars,receipts in pool.map(fetch_coinbase,('BTC','ETH','SOL','XRP')):
        allbars[asset]=bars;allreceipts[asset]=receipts
(P/'coinbase_receipts.json').write_text(json.dumps(allreceipts))
checked=[];incomplete=[]
for x in rows:
    r=x['record'];T=int(x['T']);bars=allbars[x['asset']];remaining=r['remaining_minutes']
    reference=int(T+remaining*60-960);keys=list(range(T-3660,T,60))
    if reference not in bars or any(k not in bars for k in keys):incomplete.append([x['asset'],T]);continue
    values=[bars[k] for k in keys];sigma=statistics.pstdev(math.log(b/a) for a,b in zip(values,values[1:]))
    z=(1 if r['side']=='yes' else -1)*(values[-1]/bars[reference]-1)/(sigma*math.sqrt(remaining))
    required=r.get('required_z',3 if x['lane']=='premium_z3' or x['asset']=='BTC' else 2)
    checked.append(dict(asset=x['asset'],lane=x['lane'],ticker=r['ticker'],T=T,category=x['category'],ask=r.get('signal_ask'),
        z=z,required=required,captured_z=r.get('z'),delta=None if r.get('z') is None else z-r['z'],
        qualified_now=z>=required,remaining=remaining,spot=values[-1],reference=bars[reference],sigma=sigma))
(P/'reconstructed_minutes.json').write_text(json.dumps(checked))
captured=[r for r in checked if r['delta'] is not None]
changes=[r for r in captured if (r['captured_z']>=r['required'])!=r['qualified_now']]
result=dict(reconstructed=len(checked),captured=len(captured),incomplete=incomplete,changed_z_decisions=changes)
(P/'revision_audit.json').write_text(json.dumps(result,indent=2));print(json.dumps(result,indent=2))
