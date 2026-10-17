"""Reconcile actual EURUSD fills with signal/quote/depth evidence; no orders."""
import collections as C,gzip,json,math,pickle,statistics
from decimal import Decimal as D
import analyze as a
import simulation as s
from pull import P,END
def save(n,x):s.save(n,x)
lo=1791427997
events=[r for r in a.events if lo<=r['decision_ts']<END]
actual=[r for r in json.loads((P/'actual_eurusd_orders.json').read_text()) if r['close_ts']<END]
attempts=[r for r in json.loads((P/'actual_eurusd_attempts.json').read_text()) if r['close_ts']<END]
am={r['ticker']:r for r in actual}
qualified=[r for r in events if isinstance(r.get('z'),(int,float)) and r['z']>=r['required_z']]
groups=C.defaultdict(list)
for r in qualified:groups[r['ticker']].append(r)
keys=C.defaultdict(list)
for r in events:keys[r['lane'],r['decision_ts']].append(r)
expected={(l,t) for l in ('cushion_z2','premium_z3') for t in range(math.ceil(lo/60)*60,END,60)}
fm={(m['ticker'],f['t']):f for m in a.markets for f in m['features']}
comparisons=0;source=[];quotes=[]
for r in events:
    f=fm.get((r.get('ticker'),r['decision_ts']))
    if f is None:continue
    if 'signal_ask' in r and (abs(r['signal_ask']-f['ask'])>1e-8 or abs(r['signal_bid']-f['bid'])>1e-8 or r['side']!=('yes' if f['side']==1 else 'no')):quotes.append(r)
    if 'z' in r:
        comparisons+=1
        if abs(r['z']-f['z'])>1e-6 or abs(r['spot']-f['spot'])>1e-10 or abs(r['strike_proxy']-f['ref'])>1e-10:source.append(r)
missed=[]
for ticker,rs in groups.items():
    if ticker in am:continue
    checks=[]
    for r in rs:
        side=1 if r['side']=='yes' else -1
        floor,absolute=(.85,.939) if r['lane']=='cushion_z2' else (.93,.975)
        cap=s.cap(r['signal_ask'],side,absolute,json.dumps(r['price_ranges'],sort_keys=True))
        status,known=a.observed(ticker,r['decision_ts'],side,floor,cap)
        checks.append(dict(t=r['decision_ts'],lane=r['lane'],status=status,book=known))
    missed.append(dict(ticker=ticker,checks=checks,reasons=dict(C.Counter(r.get('execution_status',r['status']) for r in rs))))
fee_checks=[];slip=[]
for r in actual:
    assert D(r['pnl'])==D(r['filled'])*(r['side']==r['result'])-D(r['cost'])-D(r['fees'])
    assert D(r['filled'])==5
    sig=r['signal'];side=1 if r['side']=='yes' else -1
    floor,absolute=(.85,.939) if r['lane']=='cushion_z2' else (.93,.975)
    cap=s.cap(sig['signal_ask'],side,absolute,json.dumps(sig['price_ranges'],sort_keys=True))
    status,book=a.observed(r['ticker'],sig['decision_ts'],side,floor,cap)
    fee_checks.append(abs(s.fee(float(r['cost'])/5)-float(r['fees']))<1e-7)
    slip.append(dict(ticker=r['ticker'],entry=float(r['cost'])/5,fees=r['fees'],book_evidence=status,
        sampled_price=book[0] if book else None,decision_to_intent=r['created_ts']-sig['decision_ts'],
        last_book_to_intent=r['created_ts']-sig.get('execution_book_ts',sig.get('book_observed_ts',r['created_ts']))))
bridges={};pred=pickle.loads((P/'predictions.pickle').read_bytes())
for mode in ('base','receipts','all_cap'):
    pool,_=a.opportunities(a.CONFIG[1],pred,mode)
    ledger,diag=s.portfolio([r for r in pool if r['asset']=='EURUSD' and lo<=r['t'] and r['close']<END],.01)
    lm={r['ticker']:r for r in ledger}
    bridges[mode]=dict(metrics=s.metrics(ledger),ledger=ledger,missing_actual=[t for t in am if t not in lm],
        extra=[r for r in ledger if r['ticker'] not in am],common_model_net=sum(r['net'] for r in ledger if r['ticker'] in am),
        common_actual_net=sum(float(am[t]['pnl']) for t in lm if t in am))
errors=[]
for k,rs in keys.items():
    if any(r['status'] in ('incomplete_spot','data_error') for r in rs):
        r=rs[-1];f=fm.get((r.get('ticker'),r['decision_ts']))
        errors.append(dict(lane=k[0],t=k[1],final_status=r['status'],rows=len(rs),historical_z=f['z'] if f else None))
summary=dict(start=lo,cutoff=END,actual_trades=len(actual),wins=sum(r['side']==r['result'] for r in actual),
    net=float(sum((D(r['pnl']) for r in actual),D(0))),fees=float(sum((D(r['fees']) for r in actual),D(0))),
    attempts=len(attempts),qualified_markets=len(groups),unfilled=len(missed),
    unfilled_with_sampled_depth=sum(any(c['status']=='supported' for c in r['checks']) for r in missed),
    unfilled_without_any_book=sum(all(c['status']=='unknown' for c in r['checks']) for r in missed),
    expected_scans=len(expected),missing_scans=sorted(expected-set(keys)),source_features_checked=comparisons,
    source_mismatches=len(source),quote_mismatches=len(quotes),fee_checks=all(fee_checks),
    median_submission_age=statistics.median(r['decision_to_intent'] for r in slip),
    max_submission_age=max(r['decision_to_intent'] for r in slip),errors=errors)
save('actual_diagnosis.json',summary);save('actual_unfilled.json',missed)
save('actual_replay_bridge.json',bridges);save('actual_fill_evidence.json',slip)
save('historical_live_mismatches.json',dict(source=source,quotes=quotes))
print(json.dumps(summary));print('BRIDGES',{m:{k:v for k,v in x.items() if k not in ('ledger','extra')} for m,x in bridges.items()})
print('EXTRA', {m:[(r['ticker'],r['evidence']) for r in x['extra']] for m,x in bridges.items()})
