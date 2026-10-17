"""Replay signal code with identical captured/public historical inputs; no execution."""
import ast, datetime, importlib.util, json, sys
from pathlib import Path

P=Path(__file__).resolve().parent
ROOT=P.parent
LIVE=ROOT/'kalshi_revalidation_2026_09_12/live_v1'
sys.path.insert(0,str(LIVE))
def module(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m
old=module('old_signal',ROOT/'kalshi_ops_five_rounds_2026_10_14/original/market_data.py')
new=module('current_signal',LIVE/'market_data.py')
bars={}
for asset,receipts in json.loads((ROOT/'kalshi_crypto_no_trades_2026_10_15/coinbase_receipts.json').read_text()).items():
    bars[asset]={int(r[0]):r for receipt in receipts for r in receipt['data']}
rows=json.loads((ROOT/'kalshi_crypto_no_trades_2026_10_15/classified.json').read_text())
reconstructed=json.loads((ROOT/'kalshi_crypto_no_trades_2026_10_15/reconstructed_minutes.json').read_text())
keys={(r['asset'],r['lane'],r['T']) for r in reconstructed}
tested=0;differences=[];outcomes={}
def iso(t):return datetime.datetime.fromtimestamp(t,datetime.timezone.utc).isoformat()
for row in rows:
    if (row['asset'],row['lane'],row['T']) not in keys:continue
    r=row['record'];T=row['T'];asset=row['asset'];close=T+60*r['remaining_minutes'];start=close-900
    side=r['side'];bid=r['signal_bid'];ask=r['signal_ask']
    yesbid,yesask=(bid,ask) if side=='yes' else (1-ask,1-bid)
    market=dict(ticker=r['ticker'],open_time=iso(start),close_time=iso(close),notional_value_dollars='1',exchange_index=2,price_ranges=r.get('price_ranges'))
    candle=dict(end_period_ts=T,yes_bid=dict(close_dollars=str(yesbid)),yes_ask=dict(close_dollars=str(yesask)))
    def get(url):
        if 'coinbase' in url:return [v for k,v in bars[asset].items() if T-95*60<=k<=T]
        if '/candlesticks?' in url:return dict(candlesticks=[candle])
        if '/markets?' in url:return dict(markets=[market])
        raise AssertionError('Unmocked GET: '+url)
    outputs=[]
    for m in (old,new):
        m.get=get;m.refresh_book=lambda x:dict(x,status='SIGNAL_QUALIFIED_NO_EXECUTION')
        o=m._scan(asset,T,set(),row['lane'])
        outputs.append({k:v for k,v in o.items() if k not in ('observed_ts','signal_qualified_ts')})
    tested+=1
    key=outputs[1]['status'];outcomes[key]=outcomes.get(key,0)+1
    if outputs[0]!=outputs[1]:differences.append(dict(asset=asset,T=T,lane=row['lane'],outputs=outputs))
def class_ast(path,name):
    return ast.dump(next(n for n in ast.parse(path.read_text()).body if isinstance(n,ast.ClassDef) and n.name==name),include_attributes=False)
cache_same=class_ast(ROOT/'kalshi_ops_five_rounds_2026_10_14/original/runner.py','MinuteQuotes')==class_ast(LIVE/'runner.py','MinuteQuotes')
result=dict(mode='IDENTICAL_INPUT_SIGNAL_REPLAY_NO_NETWORK_NO_ORDERS',tested=tested,differences=differences,outcomes=outcomes,cache_class_identical=cache_same,
    limitation='Later historical underlying bars are used identically in both versions. Tests mathematical selection, not first-arrival history or fills. Both use current lane configuration, separating intentional BTC z3 change.')
(P/'code_replay.json').write_text(json.dumps(result,indent=2));print(json.dumps(result,indent=2))
