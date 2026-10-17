"""Independent Decimal ledger audit and human-readable fixed-cohort report."""
import datetime as dt
import gzip
import json
import pickle
from collections import Counter
from decimal import Decimal as D,ROUND_CEILING,ROUND_FLOOR
from pathlib import Path
from zoneinfo import ZoneInfo
P=Path(__file__).resolve().parent;ET=ZoneInfo('America/New_York')
summary=json.loads((P/'results.json').read_text());sensitivity=json.loads((P/'sensitivity.json').read_text())
ledgers=json.load(gzip.open(P/'ledgers.json.gz'));audit=json.loads((P/'audit.json').read_text())
orders={o['ticker']:o for o in json.loads((P/'frozen_orders.json').read_text())['orders']}
inputs=pickle.load(gzip.open(P/'inputs.pkl.gz','rb'));sources=pickle.load(gzip.open(P/'seconds.pkl.gz','rb'))
entries={r['ticker']:r for r in inputs['actual']}
cash_checks=0;scalar_checks=0
for r in inputs['actual']:
    start,values=sources['WTI'];since=None;trigger=None
    for t in range(int(r['entered'])+1,int(r['close'])):
        rem=r['close']-t
        z=r['side']*((values[t-1-start]-r['reference'])/r['reference'])/(r['entry_sigma']*(rem/60)**.5)
        if rem>300 or z>=1.75:since=None
        else:
            if since is None:since=t
            if t-since>=60:trigger=t;break
    row=next(x for x in ledgers['all_wti_00'] if x['ticker']==r['ticker'])
    assert row['trigger']==trigger;scalar_checks+=1
for key,xs in ledgers.items():
    assert len(xs)==111 and {x['ticker'] for x in xs}==set(orders)
    total=D(0);saved=D(0);cost=D(0)
    for x in xs:
        o=orders[x['ticker']];hold=D(o['pnl']);net=hold
        assert D(str(x['hold_net']))==hold
        if x['exit']:
            px=D(str(x['bid_low']));q=D(o['filled']);fee=(D('.07')*q*px*(1-px)).quantize(D('.000001'),rounding=ROUND_CEILING)
            cash=(q*px-fee).quantize(D('.0001'),rounding=ROUND_FLOOR)
            net=cash-D(o['cost'])-D(o['fees'])-q*D('.01')
            assert abs(float(q*px-cash)-x['exit_fee'])<1e-10
            assert float(px)>=x['floor'];assert x['trigger']+1<x['close']
            r=entries[x['ticker']];end=((x['trigger']+1)//60+1)*60;c=inputs['markets'][x['ticker']]['candles'][end]
            bid=D(str(c['bl'])) if r['side']==1 else 1-D(str(c['ah']))
            assert bid==px
            cash_checks+=1
        assert abs(float(net)-x['net'])<1e-10
        total+=net
        if x['won']:cost+=hold-net
        else:saved+=net-hold
    assert abs(float(total)-summary[key]['net'])<1e-9
    assert total==D('15.3050')+saved-cost
for c in range(91):
    a=summary[f'original_only_{c:02d}'];b=summary[f'all_wti_{c:02d}']
    for k in a:
        if k!='reasons':assert a[k]==b[k]
receipt=dict(cohort=111,variants=len(ledgers),ledger_rows_checked=sum(len(x) for x in ledgers.values()),independent_scalar_triggers=scalar_checks,
             decimal_exit_cash_checks=cash_checks,all_scope_results_identical=True,production_preservation=json.loads((P/'preservation_before.json').read_text())==json.loads((P/'preservation_after.json').read_text()))
(P/'independent_audit.json').write_text(json.dumps(receipt,indent=2)+'\n')

lines=[]
def w(s=''):lines.append(s)
def fmt(x):return f'{x:+.4f}'
w('WTI LOWER SELL FLOORS — EXACT 106 WINNERS / 5 LOSSES')
w('Frozen actual entries: '+audit['first_close_et']+' through '+audit['last_close_et'])
w('Holding net: +$15.3050 after actual entry fees. All111 have five contracts.')
w('Original89W/5L; premium17W/0L. No new trades or capital recycling credited.')
w()
w('Finding: reducing the sell floor alone did not improve on90c in this minute-low quote scenario.89c ties90c. Floors88–86c retain some modeled improvement over holding, but add only winner cuts, not rescued losses. At85c the rule is below holding.69c is the highest tested whole-cent floor rescuing4losses, but its19winner cuts cost more than it saves. It remains below holding after fees even without friction.')
w()
w('Unchanged rule: signed z<1.75, final300seconds, continuously60seconds, checking each second. Frozen entry reference/sigma; one-second assumed feed availability and exit latency. One first confirmed attempt. If its modeled bid fails the floor, hold to settlement; no later retry or fallback. An exit is modeled at the recorded held-side minute LOW, not at the floor. Floor means minimum acceptable proceeds per contract before fees.')
w('Primary preserves original-only scope, holding premium trades. Separate all-WTI application tested all106winners and5losses: the17premium winners never confirm this rule, so every monetary result and exit count is identical between the two scopes.')
w()
w('COARSE GRID AND IMPORTANT BOUNDARIES')
w('Money saved, winner cost and friction net below include modeled exit fees plus1c per exited contract. Fee-only net is separately shown.')
w('Floor | Losses reduced | Saved on losses | Winners cut | Cost of cutting winners | Net after fees only | Net after fees+friction | Change vs hold')
w('Hold  | 0/5 | 0.0000 | 0/106 | 0.0000 | +15.3050 | +15.3050 | +0.0000')
for c in [90,89,88,87,86,85,80,75,70,69,65,60,50,40,30,20,10,0]:
    m=summary[f'all_wti_{c:02d}']
    w(f"{c:2d}c | {m['losses_reduced']}/5 | {m['losses_saved']:.4f} | {m['winners_cut']}/106 | {m['winner_cost']:.4f} | {fmt(m['fees_only_net'])} | {fmt(m['net'])} | {fmt(m['delta'])}")
w()
w('WHY WINNER COST IS MORE THAN LOST SMALL PROFITS')
for c in [90,85,69,10]:
    m=summary[f'all_wti_{c:02d}'];w(f"{c}c: {m['winners_cut']} eventual winners had ${m['cut_winners_original_profit']:.4f} holding profit; exiting makes their combined result ${m['cut_winners_exit_net']:.4f}. Difference ${m['winner_cost']:.4f}. {m['winners_turned_loss']} become losing trades after fees/friction.")
w('The difference includes both forfeited settlement profit AND new losses realized by selling below entry, plus exit costs. Entry fees are retained once; they are not charged again.')
w()
w('FIVE ACTUAL LOSSES AT THE FIRST CONFIRMED SIGNAL')
for x in ledgers['all_wti_00']:
    if x['won']:continue
    w(f"{x['ticker']} | hold {fmt(x['hold_net'])} | {x['remaining']}s left | z{x['trigger_z']:.6f} | minute bid{x['bid_low']*100:g}–{x['bid_high']*100:g}c | at low saved{x['delta']:.4f} | exit net{fmt(x['net'])}")
w()
w('ALL WINNER CUTS AT69c; includes all costs')
for x in ledgers['all_wti_69']:
    if x['won'] and x['exit']:
        w(f"{x['ticker']} | minute low{x['bid_low']*100:g}c | hold{fmt(x['hold_net'])} | exit{fmt(x['net'])} | damage{-x['delta']:.4f}")
w()
w('SENSITIVITY: floor | standard net |2c friction net |3second order latency net')
for c in [90,85,80,75,70,65,60,50,40,30,20,10,0]:
    key=f'all_wti_{c:02d}';a=summary[key];b=sensitivity[key]
    w(f"{c}c | {fmt(a['net'])} | {fmt(b['friction2c']['net'])} | {fmt(b['order_latency3s']['net'])}")
w()
w('CALENDAR-DAY ACCOUNTING: date | holding |90c |69c | no floor (fees+friction)')
days=sorted({dt.datetime.fromtimestamp(x['close'],ET).date().isoformat() for x in ledgers['all_wti_00']})
for day in days:
    def select(key):return [x for x in ledgers[key] if dt.datetime.fromtimestamp(x['close'],ET).date().isoformat()==day]
    base=select('all_wti_00');w(day+' | '+fmt(sum(x['hold_net'] for x in base))+' | '+' | '.join(fmt(sum(x['net'] for x in select(f'all_wti_{c:02d}'))) for c in [90,69,0]))
w()
w('DATA AND LIMITS')
w('111 complete reconstructed underlying second paths;42,668 z values independently reproduced; all111 first triggers agree with scalar and vector replay. Exact prior five-loss triggers and quote lows reproduced. The new23:45ET winner also meets this exit condition: minute low56c, so it is cut at floors<=56c; the new23:00ET winner never confirms it.')
w('Historical quotes have minute resolution, not per-second executable depth. The bid-low floor acceptance and exit prices are scenario assumptions; no five-contract exit fill is proven. Minute lows may occur before or after the signal. A rejected low does not prove a higher limit never filled inside that minute. No minute-high fills are credited. Source first-arrival timestamps/corrections are not fully observed for these reconstructed paths; one-second availability is assumed.')
w('Therefore these are consistent comparisons with the previous replay, not a realistic fill-verified live backtest or hard PnL bounds. Existing rules and floor choices have seen these same outcomes; no untouched holdout. Small five-loss sample; no future edge claim or recommendation to deploy.')
w('All actual entry costs and fees are from the frozen ledger. Exit fees:7%*q*p*(1-p), six-decimal ceiling and four-decimal cash rounding. Public KXWTI15M series metadata verified quadratic/multiplier1; historical metadata changes are not assumed disproven by the current receipt.1c friction is separate from exchange fees, only for exited contracts. No order splitting/queue priority proof.')
w('Official documentation checked2026-10-15: https://docs.kalshi.com/getting_started/fee_rounding and https://docs.kalshi.com/api-reference/market/get-market-candlesticks')
w()
w('VALIDATION AND PRESERVATION')
w(json.dumps(receipt,sort_keys=True))
w('25production source files, all ARM/HALT flags and five service PIDs/restarts unchanged. No financial endpoint calls, live imports, orders, service restarts, paused-lane reactivation or production edits. Research outputs only.')
w('replay.py reproduces182scenario ledgers; results.json retains every whole-cent floor in both scopes. ledgers.json.gz contains every trade including failed/no-signal attempts. fresh/ contains the public receipts for the two additional winners and fee metadata. inputs.pkl.gz/seconds.pkl.gz contain the frozen reduced WTI inputs. audit_report.py performs an independent per-row Decimal cash audit and scalar trigger replay. preservation snapshots/hash manifest retain evidence.')
(P/'REPORT.txt').write_text('\n'.join(lines)+'\n')
print(json.dumps(receipt,indent=2))
