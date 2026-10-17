import gzip,json,pickle,math,hashlib
from pathlib import Path
from collections import Counter
from decimal import Decimal as D
import study as s
P=s.P;market={m['ticker']:m for m in pickle.loads((P/'features.pickle').read_bytes())};checks=Counter()
for filename in ('value_execution_ledgers.json.gz','survivor_ledgers.json.gz','book_overlay_ledgers.json.gz','exit_ledgers.json.gz'):
 with gzip.open(P/filename,'rt') as f:data=json.load(f)
 def walk(x):
  if isinstance(x,dict):
   for v in x.values():walk(v)
  elif isinstance(x,list) and x and isinstance(x[0],dict) and 'ticker' in x[0]:
   assert len({r['ticker'] for r in x})==len(x)
   for r in x:
    assert r['t']<r['entered']<r['close']
    assert r['floor']-1e-8<=r['entry']<=r['cap']+1e-8
    if r.get('exit_kind','settlement')=='settlement':payout=float(r['won']);assert r['settled']>=r['close']
    else:
     assert r['entered']<r['settled']<r['close'];U=r['settled']-3;assert int(U)%60==0
     m=market[r['ticker']];q=s.quote(m['candles'][int(U)]);n=s.quote(m['candles'][int(U)+60],'o');price=min(q[0],n[0]) if r['side']==1 else min(1-q[1],1-n[1]);assert abs(r['exit_price']-price)<1e-9
     exit_fee=s.fee(price,5,m['multiplier']);assert abs(exit_fee-r['exit_fee'])<1e-9
     payout=price-exit_fee/5-r['exit_friction']/5;assert abs(payout-r['payout'])<1e-9;checks['exits']+=1
    net=5*(D(str(payout))-D(str(r['entry'])))-D(str(r['fee']))-D(str(r['friction']))
    assert abs(float(net)-r['net'])<1e-8
    checks['trade_scenario_rows']+=1
 walk(data)
with gzip.open(P/'value_execution_ledgers.json.gz','rt') as f:rr=json.load(f)['recorded_overlay']['base']['pooled']
# Conservative missed-fill stress: keep every loss, remove the most profitable quarter of winners.
w=sorted((r for r in rr if r['won']),key=lambda r:r['net'],reverse=True);drop={r['ticker'] for r in w[:math.ceil(len(w)*.25)]};subset=[r for r in rr if r['ticker'] not in drop]
late=[r for r in rr if r['close']>=s.PERIODS['confirmation'][0]]
summary=dict(passed=True,checks=checks,copper_value_later_combined=s.metrics(late),copper_value_drop_best_quarter_winners=s.metrics(subset),dropped_winners=len(drop),actual_model_signal_dates=[s.day(min(r['t'] for r in rr)),s.day(max(r['t'] for r in rr))])
s.save('extended_validation.json',summary)
spec=dict(status='RESEARCH_ONLY_NOT_ACTIVATED',asset='COPPER',series='KXCOPPER15M',candidate_id=40,name='five-minute empirical value',decision_minutes_remaining=5,training='expanding Copper data from Sep1, retrain Monday00UTC, only previously settled markets, minimum250 matching-horizon observations',residual='log(official_expiration_price/decision_spot)/(std(last60 completed log returns)*sqrt(5))',probability='Beta(1,1)-smoothed empirical tail probability using rounded settlement threshold; shrink80% empirical/20% decision market midpoint',entry='Choose side with largest probability minus ask minus current taker fee/5 minus1c; enter only if >=2c, ask50–<95c, spread<=2c',price_cap='min(signal ask+1c,93.9c), rounded toward lower spending on market price ladder',quantity=5,portfolio=dict(initial_cash=100,max_contracts=25,max_reserved_dollars=25,daily_loss_including_open=20,min_cash=5),exit='hold to official settlement',signal_samples=101,quote_proxy_trades=100,live_orders=0,selection_note='Rule40 was an early-period survivor, third-ranked Copper candidate, examined after the top-ranked timing variant failed execution stress. Final priority is exploratory and not an untouched holdout.',required_next='Prospective decision-time quote/feed receipts, actual later depth at5 contracts under cap, source revisions/arrival gaps, trade outcomes and modeled fees; compare calibrated probability against realized outcomes. No current live change authorized by this research.')
s.save('candidate_spec.json',spec)
print(json.dumps(summary,indent=2))
