"""Freeze the chosen ETH rule, then fetch a bounded newer public sample."""
import datetime as dt,importlib.util,json,time
import numpy as np
import study as s
P=s.P;folder=P/'fresh_holdout';folder.mkdir(exist_ok=True)
spec=importlib.util.spec_from_file_location('public_pull',P/'public_pull.py');pull=importlib.util.module_from_spec(spec);spec.loader.exec_module(pull)
receipt=P/'fresh_frozen_spec.json'
if receipt.exists():frozen=json.loads(receipt.read_text())
else:
    frozen=dict(config=s.configs()[252],start=1791060301,end=int(time.time()),selection_sha256=s.sha(P/'frozen_selection.json'),frozen_at=dt.datetime.now(dt.timezone.utc).isoformat());s.save(receipt.name,frozen)
pull.ROOT=folder;pull.START=frozen['start'];pull.END=frozen['end'];pull.kalshi('ETH')
markets=[json.loads(l) for l in (folder/'markets_ETH.jsonl').read_text().splitlines()];bars={r['ticker']:{int(c['t']):c for c in r['candles']} for r in (json.loads(l) for l in (folder/'candles_ETH.jsonl').read_text().splitlines())};mm=[];x=np.full((len(markets),14,10),np.nan)
def quote(c,field):
    if not c:return None
    try:b,a=float(c['b'+field]),float(c['a'+field])
    except (ValueError,TypeError,KeyError):return None
    return (b,a) if 0<=b<=a<=1 else None
for i,m in enumerate(markets):
    start=pull.ts(m['open_time']);close=pull.ts(m['close_time']);settled=pull.ts(m.get('settlement_ts') or m.get('expiration_time') or m['close_time']);mm.append(dict(ticker=m['ticker'],asset='ETH',start=start,close=close,settled=max(close+30,settled),result=1 if m['result']=='yes' else -1))
    for j in range(14):
        T=start+60*(j+1);cs=bars[m['ticker']];a=quote(cs.get(T),'c');b=quote(cs.get(T+60),'o');c=quote(cs.get(T+60),'c') if j<13 else None
        if a:x[i,j,:2]=a
        if b:x[i,j,2:4]=b
        if c:x[i,j,4:6]=c
np.save(P/'fresh_arrays.npy',x);s.save('fresh_markets.json',mm);d=s.features(x,mm);rows=s.rows(frozen['config'],d);s.save('fresh_trades.json',rows);s.save('fresh_results.json',dict(spec=frozen,quote=s.metrics(rows,len(mm)),cost1c=s.metrics(rows,len(mm),.01),note='Newly downloaded after rule freeze, but only a few hours. Quote proxy, not a prospective fill test.'))
print('newer sample',len(mm),'markets',len(rows),'trades',s.metrics(rows,len(mm))['net'])
