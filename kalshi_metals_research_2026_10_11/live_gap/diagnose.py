"""Forensic Copper replay/live reconciliation. Cached read-only inputs; no orders."""
import collections as C
import datetime as dt
import gzip
import json
import math
import pickle
import sqlite3
import sys
from decimal import Decimal as D
from pathlib import Path

OUT = Path(__file__).resolve().parent
P = OUT.parent
sys.path.insert(0, str(P))
import study as s

def save(name, data):
    (OUT/name).write_text(json.dumps(data, indent=2, allow_nan=False)+'\n')

def gz(name, data):
    with gzip.open(OUT/name, 'wt') as f: json.dump(data, f)

def stamps(t): return dt.datetime.fromtimestamp(t, s.ET).isoformat()

def book(levels, floor, cap):
    q = D(0); cost = D(0); below = False; best = None
    for px, n in sorted((1-D(p), D(n)) for p,n in levels):
        if n <= 0: continue
        if best is None: best = px
        if px < D(str(floor)): below = True
        if px > D(str(cap)): break
        take = min(D(5)-q,n); q += take; cost += take*px
        if q == 5: break
    return dict(enough=q==5 and not below, depth=float(q), below_floor=below,
                best=float(best) if best is not None else None,
                average=float(cost/q) if q else None)

period = json.loads((P/'comparison/period_inputs.json').read_text())
lo,hi = period['activated_ts'],period['halt_request_ts']
db = sqlite3.connect((P/'live_snapshot.sqlite3').as_uri()+'?mode=ro',uri=True)
events = [dict(json.loads(raw),_saved_ts=ts,_id=i) for i,ts,raw in db.execute(
    'select id,ts,event from events where ts>=? and ts<? order by id',(lo,hi))]
cu = [r for r in events if r.get('asset')=='COPPER']
orders = [json.loads(x[0]) for x in db.execute('select record from orders')]
actual = [r for r in orders if r.get('asset')=='COPPER' and lo<=r['created_ts']<hi]
attempts = [json.loads(x[0]) for x in db.execute('select record from order_attempts')]
attempts = [r for r in attempts if r.get('asset')=='COPPER' and lo<=r['created_ts']<hi]
actualmap = {r['ticker']:r for r in actual}
markets = pickle.loads((P/'features.pickle').read_bytes())
lookup = {m['ticker']:m for m in markets if m['asset']=='COPPER'}
feature = {(m['ticker'],f['t']):f for m in lookup.values() for f in m['features']}
qualified = [r for r in cu if isinstance(r.get('z'),(int,float)) and r['z']>=r['required_z']]
qmap=C.defaultdict(list)
for r in qualified:qmap[r['ticker']].append(r)
keys=C.defaultdict(list)
for r in cu:keys[r['lane'],r['decision_ts']].append(r)
start=math.ceil(lo/60)*60;stop=int(hi//60)*60
expected={(lane,t) for lane in ('cushion_z2','premium_z3') for t in range(start,stop,60)}
missing=expected-set(keys)
features_checked=0;quote_mismatches=[];source_mismatches=[]
for r in cu:
    f=feature.get((r.get('ticker'),r['decision_ts']))
    if f is None:continue
    if 'signal_ask' in r:
        if (r['side']!=('yes' if f['side']==1 else 'no') or
            abs(r['signal_ask']-f['ask'])>1e-8 or abs(r['signal_bid']-f['bid'])>1e-8):quote_mismatches.append(r)
    if 'z' in r:
        features_checked+=1
        if abs(r['z']-f['z'])>1e-6 or abs(r['spot']-f['spot'])>1e-10 or abs(r['strike_proxy']-f['ref'])>1e-10:
            source_mismatches.append(r)
observer=[]
with (P.parent/'kalshi_execution_retry_2026_10_02/observer_state/observations.jsonl').open() as f:
    for line in f:
        r=json.loads(line)
        if r.get('ticker','').startswith('KXCOPPER15M') and lo<=r.get('decision_ts',0)<hi:observer.append(r)
gz('events.json.gz',cu);gz('observer.json.gz',observer)
receipt_books=C.defaultdict(list)
for r in qualified:
    floor=.8 if r['lane']=='cushion_z2' else .93
    absolute=.939 if r['lane']=='cushion_z2' else .975
    side=1 if r['side']=='yes' else -1
    cap=s.cap(r['signal_ask'],side,absolute,json.dumps(r['price_ranges'],sort_keys=True))
    for kt,kl in (('book_observed_ts','book_levels'),('execution_book_ts','execution_book_levels')):
        if kt in r and kl in r and 0<=r[kt]-r['decision_ts']<=25:
            receipt_books[r['ticker']].append(dict(t=r[kt],decision_ts=r['decision_ts'],source=kt,
                cap=cap,floor=floor,side=r['side'],**book(r[kl],floor,cap)))
    for ob in observer:
        if (ob['ticker']==r['ticker'] and ob['decision_ts']==r['decision_ts'] and ob['lane']==r['lane']
            and 0<=ob['observed_ts']-r['decision_ts']<=25):
            receipt_books[r['ticker']].append(dict(t=ob['observed_ts'],decision_ts=r['decision_ts'],source='observer',
                cap=cap,floor=floor,side=r['side'],**book(ob.get('levels',[]),floor,cap)))
receipt_books={t:list({(x['t'],x['source'],x['decision_ts']):x for x in rows}.values()) for t,rows in receipt_books.items()}
save('book_checks.json',receipt_books)
misses=[]
for t,rs in qmap.items():
    if t in actualmap:continue
    first=rs[0];books=receipt_books[t];caps=sorted({x['cap'] for x in books})
    won=lookup[t]['result']==int(first['side']=='yes');cap=caps[0]
    misses.append(dict(ticker=t,side=first['side'],won=won,first_signal_et=stamps(first['decision_ts']),
        signal_ask=first['signal_ask'],cap=cap,book_count=len(books),passing_books=sum(x['enough'] for x in books),
        best_sampled_ask=min(x['best'] for x in books if x['best'] is not None),
        reasons=dict(C.Counter(r.get('execution_status',r['status']) for r in rs)),
        hypothetical_cap_net=5*(won-cap)-s.fee(cap,5,lookup[t]['multiplier']),
        hypothetical_cap_net_plus1c=5*(won-cap)-s.fee(cap,5,lookup[t]['multiplier'])-.05))
save('qualified_unfilled.json',misses)

with gzip.open(P/'comparison/ledgers.json.gz','rt') as f:all_ledgers=json.load(f)
old=all_ledgers['actual_copper_operating_window']['old']['observed_overlay']['cost1'];oldmap={r['ticker']:r for r in old}
bridge_rows=[]
for r in actual:
    x=oldmap.get(r['ticker']);assert x is not None
    bridge_rows.append(dict(ticker=r['ticker'],actual_net=float(r['pnl']),replay_net=x['net'],
        actual_entry=float(r['cost'])/5,replay_entry=x['entry'],actual_fee=float(r['fees']),replay_fee=x['fee'],
        friction=x['friction'],actual_decision=r['signal']['decision_ts'],replay_decision=x['t'],
        same_side=r['side']==('yes' if x['side']==1 else 'no'),actual_won=r['side']==r['result'],replay_won=bool(x['won'])))
extra=[r for r in old if r['ticker'] not in actualmap]
actual_net=float(sum((D(r['pnl']) for r in actual),D(0)))
common=sum(x['net'] for x in old if x['ticker'] in actualmap)
bridge=dict(actual_net=actual_net,friction_on_actual15=-.75,
    model_price_and_fee_delta=common-(actual_net-.75),common15_replay_net=common,
    added3_quote_only_winners=sum(r['net'] for r in extra),final_replay_net=sum(r['net'] for r in old),
    rows=bridge_rows,extra=extra)
assert abs(bridge['final_replay_net']-(-4.0631))<1e-8
assert all(r['same_side'] and r['actual_won']==r['replay_won'] for r in bridge_rows)
save('pnl_bridge.json',bridge)

basepool,_=s.opportunities(list(lookup.values()),s.CONFIG[1],{},'base')
quoteledger,_=s.portfolio([r for r in basepool if lo<=r['entered']<hi],.01)
quote_metrics=s.metrics(quoteledger)
save('quote_only_replay.json',dict(metrics=quote_metrics,ledger=quoteledger,
    missing_actual=[r['ticker'] for r in actual if r['ticker'] not in {x['ticker'] for x in quoteledger}]))

incomplete=[]
for key,rr in keys.items():
    if not any(r['status'] in ('incomplete_spot','data_error') for r in rr):continue
    r=rr[-1];f=feature.get((r.get('ticker'),r['decision_ts']))
    incomplete.append(dict(lane=key[0],t=key[1],et=stamps(key[1]),attempts=len(rr),final_status=r['status'],
        historical_z=f['z'] if f else None,required_z=r.get('required_z',1.5 if key[0]=='cushion_z2' else 2.5),
        source_note='Historical chart may be revised; does not prove decision-time data availability'))
no_current_while_listed=[]
for r in cu:
    if r['status']!='no_current_market':continue
    T=r['decision_ts']
    mm=[m for m in lookup.values() if m['start']<=T<m['close'] and 1<=(m['close']-T)/60<=10]
    if mm:no_current_while_listed.append(dict(t=T,lane=r['lane'],tickers=[m['ticker'] for m in mm]))

winners=[r for r in actual if r['side']==r['result']];losers=[r for r in actual if r['side']!=r['result']]
latency=[r['created_ts']-r['signal']['decision_ts'] for r in actual]
cashcost=sum(D(r['cost'])+D(r['fees']) for r in actual)
economics=dict(trades=len(actual),wins=len(winners),losses=len(losers),net=actual_net,
    winners_net=float(sum((D(r['pnl']) for r in winners),D(0))),losers_net=float(sum((D(r['pnl']) for r in losers),D(0))),
    average_winner=float(sum((D(r['pnl']) for r in winners),D(0))/len(winners)),
    average_loser=float(sum((D(r['pnl']) for r in losers),D(0))/len(losers)),
    actual_allin_breakeven=float(cashcost/(5*len(actual))),win_rate=len(winners)/len(actual),
    median_submission_age=sorted(latency)[len(latency)//2],max_submission_age=max(latency),
    exchange_submissions=len(attempts),full_five_fills=sum(D(r['filled'])==5 for r in actual),
    actual_plus_all6_misses_at_cap_no_extra_friction=actual_net+sum(r['hypothetical_cap_net'] for r in misses),
    actual_plus_all6_misses_at_cap_with1c=actual_net+sum(r['hypothetical_cap_net_plus1c'] for r in misses))
fees_match=[abs(s.fee(float(r['cost'])/5,5,lookup[r['ticker']]['multiplier'])-float(r['fees']))<1e-7 for r in actual]
for r in actual:
    assert D(r['pnl'])==D(r['filled'])*(r['side']==r['result'])-D(r['cost'])-D(r['fees'])
    assert D(r['filled'])==5 and r['attempt_number']==1

hist=all_ledgers['common_since_model_ready']['old']['observed_overlay']['cost1']
pre=[r for r in hist if r['entered']<lo]
preloss=sum(not r['won'] for r in pre)/len(pre)
p2=1-(1-preloss)**15-15*preloss*(1-preloss)**14
rolling=[sum(x['net'] for x in pre[i:i+15]) for i in range(len(pre)-14)]
regimes={}
for name,a,b in [('early',s.ts('2026-09-14T00:00:00Z'),s.ts('2026-09-21T00:00:00Z')),
                 ('selection',s.ts('2026-09-21T00:00:00Z'),s.ts('2026-10-03T00:00:00Z')),
                 ('later',s.ts('2026-10-03T00:00:00Z'),s.END)]:
    rr=[r for r in hist if a<=r['entered']<b];regimes[name]=s.metrics(rr)
stats=dict(prelive=s.metrics(pre),iid_probability_at_least2_losses_in15=p2,
    rolling15_blocks=len(rolling),fraction_rolling15_negative=sum(v<0 for v in rolling)/len(rolling),
    fraction_rolling15_at_or_below_actual=sum(v<=actual_net for v in rolling)/len(rolling),worst_rolling15=min(rolling),
    caveat='Descriptive overlapping historical blocks, not independent tests; IID probability assumes constant independent loss rate. Rules selected on prior history and live stopped after a loss, so neither is a valid significance test.',regimes=regimes)

loss_details=[]
for r in losers:
    sig=r['signal'];f=feature[r['ticker'],sig['decision_ts']];p=.5*(1+math.erf(sig['z']/math.sqrt(2)))
    loss_details.append(dict(ticker=r['ticker'],net=r['pnl'],side=r['side'],z=sig['z'],minutes=sig['remaining_minutes'],
        entry=float(r['cost'])/5,signal_ask=sig['signal_ask'],fee=float(r['fees']),
        sigma=f['sigma'],short_z=f['zrob'],multiminute_z=f['zh'],normal_win_estimate=p,
        actual_breakeven=float((D(r['cost'])+D(r['fees']))/5),
        final_book_to_intent=r['created_ts']-sig['execution_book_ts'],submission_age=r['created_ts']-sig['decision_ts'],
        warning='Gaussian number is a model diagnostic, not a calibrated true probability.'))
save('loss_details.json',loss_details)
summary=dict(period=dict(start_et=stamps(lo),end_et=stamps(hi)),economics=economics,
    funnel=dict(qualified_markets=len(qmap),filled_markets=len(actual),unfilled_markets=len(misses),
                sampled_executable_unfilled=sum(any(b['enough'] for b in receipt_books[m['ticker']]) for m in misses)),
    scans=dict(expected_complete_lane_minutes=len(expected),missing_complete_lane_minutes=len(missing),missing=list(missing),
               records=len(cu),first_status=dict(C.Counter(v[0]['status'] for k,v in keys.items() if k in expected)),
               source_feature_rows_checked=features_checked,source_mismatches=len(source_mismatches),quote_mismatches=len(quote_mismatches),
               no_current_while_historical_market_open=len(no_current_while_listed),source_errors=incomplete),
    arithmetic=dict(all15_fee_models_match=all(fees_match),all15_pnl_match=True),statistics=stats,
    quote_only_net=quote_metrics['net'],replay_overlay_net=bridge['final_replay_net'])
save('summary.json',summary);save('historical_vs_live_mismatches.json',dict(source=source_mismatches,quote=quote_mismatches,no_current=no_current_while_listed))
print(json.dumps({k:summary[k] for k in ('economics','funnel','scans','arithmetic','quote_only_net','replay_overlay_net')},indent=2))
print('MISSES',json.dumps(misses,indent=2));print('BRIDGE',{k:v for k,v in bridge.items() if k not in ('rows','extra')})
print('HISTORY', {k:v for k,v in stats.items() if k not in ('regimes','prelive')})
print('REGIMES',{n:{k:r[k] for k in ('trades','wins','losses','net','breakeven')} for n,r in regimes.items()})
