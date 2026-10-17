"""Full raw-input reconstruction, distinct from live ledger filtering.

Backfilled candles can differ from the version available at decision time.
Every comparison with live observed features is recorded, not silently replaced.
No execution/P&L forecast is made from minute quotes.
"""
import json, math, statistics, sqlite3, sys
from pathlib import Path
from collections import defaultdict, Counter
from datetime import datetime
from zoneinfo import ZoneInfo

O=Path(__file__).resolve().parent
P=Path('/home/varma/kalshi_operations_audit_2026_10_05')
sys.path.insert(0,str(O/'candidate'))
import market_data
from lanes import Z2, Z3, LANES
ET=ZoneInfo('America/New_York')
def stamp(s):return int(datetime.fromisoformat(s.replace('Z','+00:00')).timestamp())
def et(t):return datetime.fromtimestamp(t,ET).isoformat()
start=int(json.loads((P/'z3_receipt.json').read_text())['verified_ts']//60)*60
end=int(json.loads((P/'capture.json').read_text())['captured_ts']//60)*60
db=sqlite3.connect((P/'snapshot.sqlite3').as_uri()+'?mode=ro',uri=True)
orders={json.loads(r[0])['ticker']:json.loads(r[0]) for r in db.execute('select record from orders')}
live=defaultdict(list)
for raw, in db.execute('select event from events where ts>=? order by id',(start,)):
    e=json.loads(raw)
    if e.get('ticker'):live[(e['lane'],e['ticker'],e['decision_ts'])].append(e)
markets=json.loads((P/'public/markets_all.json').read_text())
candles={}
for f in sorted((P/'public').glob('candles_*.json')):
    for row in json.loads(f.read_text())['markets']:
        candles[row['market_ticker']]={c['end_period_ts']:c for c in row['candlesticks']}
spot={};spot_duplicates=[]
for asset in market_data.ASSETS:
    values={}
    for f in sorted((P/'public').glob(f'spot_{asset}_*.json')):
        for bar in json.loads(f.read_text()):
            T=int(bar[0]);px=float(bar[4])
            if T in values and values[T]!=px:spot_duplicates.append((asset,T,values[T],px))
            values[T]=px
    spot[asset]=values
features=[];issues=[];parity=[];observed_differences=[]
original_book=market_data.refresh_book
market_data.refresh_book=lambda rec:dict(rec,status='qualified_filters')
for m in markets:
    ticker=m['ticker'];asset=ticker.split('-')[0][2:-3];open_ts=stamp(m['open_time']);close=stamp(m['close_time'])
    for T in range(max(start,open_ts+5*60), min(end,close-3*60)+1,60):
        c=candles.get(ticker,{}).get(T)
        if not c:
            issues.append(dict(ticker=ticker,T=T,error='missing_candle'));continue
        try:
            b=float(c['yes_bid']['close_dollars']);a=float(c['yes_ask']['close_dollars'])
            assert 0<=b<=a<=1
            keys=list(range(T-61*60,T,60));bars=spot[asset]
            values=[bars[k] for k in keys];strike=bars[open_ts-60]
            sigma=statistics.pstdev(math.log(y/x) for x,y in zip(values,values[1:]))
            side='yes' if a+b>1 else 'no';bid,ask=(b,a) if side=='yes' else (1-a,1-b)
            remaining=(close-T)/60
            z=(1 if side=='yes' else -1)*(values[-1]/strike-1)/(sigma*math.sqrt(remaining))
        except (KeyError,ValueError,TypeError,AssertionError,ZeroDivisionError) as exc:
            issues.append(dict(ticker=ticker,T=T,error=repr(exc)));continue
        row=dict(ticker=ticker,asset=asset,decision_ts=T,decision_et=et(T),remaining=remaining,side=side,ask=ask,bid=bid,z=z,spot=values[-1],sigma=sigma,reference=strike,result=m.get('result'))
        for lane,rule in LANES.items():
            qualified=(float(rule.floor)-1e-8<=ask<float(rule.ceiling)-1e-8 and ask-bid<=.02+1e-8 and z>=rule.z)
            row[lane]=qualified
            # Exact scanner replay against same raw data, ignoring market ownership.
            def fake_get(url):
                if 'api.exchange.coinbase.com' in url:
                    return [[k,0,0,0,v,0] for k,v in bars.items() if T-95*60<=k<=T]
                if '/candlesticks?' in url:return {'candlesticks':list(candles[ticker].values())}
                if '/markets?' in url:return {'markets':[m]}
                raise AssertionError(url)
            market_data.get=fake_get
            rec=market_data.scan(asset,T,set(),lane=lane)
            if (rec['status']=='qualified_filters')!=qualified:
                parity.append(dict(ticker=ticker,T=T,lane=lane,computed=qualified,scanner=rec))
            if 'z' in rec and abs(z-rec['z'])>1e-10:raise AssertionError('raw scanner z discrepancy')
            obs=live.get((lane,ticker,T),[])
            if obs:
                e=obs[0]
                row[lane+'_live_status']=e['status']
                if 'signal_ask' in e and (abs(e['signal_ask']-ask)>1e-9 or e['side']!=side):
                    observed_differences.append(dict(ticker=ticker,T=T,lane=lane,kind='quote',live_ask=e['signal_ask'],backfill_ask=ask,live_side=e['side'],backfill_side=side))
                if 'z' in e and abs(e['z']-z)>1e-8:
                    observed_differences.append(dict(ticker=ticker,T=T,lane=lane,kind='z',live=e['z'],backfill=z))
        features.append(row)
out=dict(start_et=et(start),end_et=et(end),markets=len(markets),features=len(features),issues=issues,spot_duplicate_conflicts=spot_duplicates,
    scanner_decisions=len(features)*2,scanner_mismatches=parity,live_feature_differences=observed_differences,lanes={})
for lane,rule in LANES.items():
    q=[r for r in features if r[lane]]
    markets_q=sorted({r['ticker'] for r in q})
    ownership=[]
    for ticker in markets_q:
        qq=[r for r in q if r['ticker']==ticker];order=orders.get(ticker)
        before_entry=[r for r in qq if not order or r['decision_ts']<order['created_ts']]
        actual_qualified=[e for (ln,t,T),ee in live.items() if ln==lane and t==ticker for e in ee if e.get('z',-999)>=rule.z]
        ownership.append(dict(ticker=ticker,qualified_minutes=len(qq),first_qualified_et=qq[0]['decision_et'],ledger_lane=order.get('lane') if order else None,ledger_status=order.get('status') if order else None,
            order_et=et(order['created_ts']) if order else None,all_signals_after_entry=not before_entry,
            live_qualified=bool(actual_qualified),live_statuses=dict(Counter(r.get(lane+'_live_status','absent') for r in qq)),qualifying=qq))
    out['lanes'][lane]=dict(qualified_minutes=len(q),qualified_markets=len(markets_q),markets=ownership,
       z_pass_minutes=sum(r['z']>=rule.z for r in features),
       z_pass_price_too_high=sum(r['z']>=rule.z and r['ask']>=float(rule.ceiling)-1e-8 for r in features),
       z_pass_price_too_low=sum(r['z']>=rule.z and r['ask']<float(rule.floor)-1e-8 for r in features),
       by_asset={a:dict(minutes=sum(r['asset']==a for r in q),markets=len({r['ticker'] for r in q if r['asset']==a})) for a in market_data.ASSETS})
(O/'reconstructed_features.json').write_text(json.dumps(features)+'\n')
(O/'signal_reconstruction.json').write_text(json.dumps(out,indent=2)+'\n')
print(json.dumps({**{k:v for k,v in out.items() if k not in ('lanes','live_feature_differences')},
    'live_feature_difference_counts':dict(Counter(d['kind'] for d in observed_differences)),
    'lanes':{k:{kk:vv for kk,vv in v.items() if kk!='markets'} for k,v in out['lanes'].items()}},indent=2))
