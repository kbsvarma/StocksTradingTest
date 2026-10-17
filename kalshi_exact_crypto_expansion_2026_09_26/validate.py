import sys,json,math,collections
from pathlib import Path
P=Path(__file__).resolve().parent;sys.path.insert(0,str(P/'reference'));import market_data as live
Q=P.parent/'kalshi_full_review_2026_09_26/candidate_data';checked=0
for asset in ['DOGE','BNB','NEAR','ZEC','HYPE']:
 ms={m['ticker']:m for m in json.loads((Q/f'KX{asset}15M_markets.json').read_text())};qs={r['ticker']:r['candles'] for r in map(json.loads,(Q/f'KX{asset}15M_candles.jsonl').read_text().splitlines())};spot=[x for b in map(json.loads,(P/'data'/f'spot_{asset}.jsonl').read_text().splitlines()) for x in b['rows']]
 for r in json.loads((P/f'{asset}_o_trades.json').read_text()):
  T=r['signal_ts'];t=r['ticker'];m=ms[t]
  def get(url):
   if 'coinbase.com' in url:return spot
   if '/orderbook' in url:return {'orderbook_fp':{'yes_dollars':[[str(1-r['entry']),'100']], 'no_dollars':[[str(1-r['entry']),'100']]}}
   if '/candlesticks' in url:return {'candlesticks':[{'end_period_ts':c['t'],'yes_bid':{'close_dollars':c['bc']},'yes_ask':{'close_dollars':c['ac']}} for c in qs[t]]}
   return {'markets':[m]}
  live.get=get;live.time.time=lambda:T+3
  rr=live.scan(asset,T,set());assert rr['status']=='paper_fill',(asset,t,rr)
  assert abs(rr['z']-r['z'])<1e-9,(rr,r);assert rr['side']==r['side'];checked+=1
(P/'validation.json').write_text(json.dumps({'checked_signal_entries':checked,'errors':[],'method':'Replay mocked GET responses through a read-only copy of deployed market_data.scan. Mock book verifies calculation plumbing, not historical depth.'},indent=2));print('Validated against deployed scanner:',checked)
