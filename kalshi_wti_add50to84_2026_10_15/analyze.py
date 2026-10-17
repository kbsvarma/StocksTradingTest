"""Offline fixed-cohort, fixed-z/time, add-five price-cap extension."""
import datetime as dt
import gzip
import hashlib
import json
import math
import pickle
from collections import Counter
from decimal import Decimal as D, ROUND_CEILING
from pathlib import Path
from zoneinfo import ZoneInfo
import numpy as np

P=Path(__file__).resolve().parent
B=P.parent/'kalshi_wti_sell_floors_2026_10_15'
OLD=P.parent/'kalshi_wti_add5_2026_10_15'
ET=ZoneInfo('America/New_York')
SCENARIOS={'feed1_order1':(1,1),'feed1_order3':(1,3),
           'feed1_order5':(1,5),'feed3_order3':(3,3)}

def save(name, value):
    (P/name).write_text(json.dumps(value,indent=2,allow_nan=False)+'\n')

def stamp(t):
    value=t['created_time'].replace('Z','+00:00')
    return (dt.datetime.strptime(value,'%Y-%m-%dT%H:%M:%S.%f%z')
            if '.' in value else dt.datetime.fromisoformat(value)).timestamp()

def cash(cap):
    p=D(cap)/100
    nominal=(D('.07')*5*p*(1-p)).quantize(D('.000001'),rounding=ROUND_CEILING)
    spent=(5*p+nominal).quantize(D('.0001'),rounding=ROUND_CEILING)
    return dict(cap=cap,quantity=5,cost=float(5*p),nominal_fee=float(nominal),
                fee=float(spent-5*p),cash_spent=float(spent))

def z_at(row, times, source, source_lag):
    start, prices=source
    times=np.asarray(times,dtype=float)
    indices=np.floor(times-source_lag).astype(np.int64)-start
    assert ((indices>=0)&(indices<len(prices))).all()
    spots=prices[indices]
    remaining=row['close']-times
    z=row['side']*(spots/row['reference']-1)/(row['entry_sigma']*np.sqrt(np.maximum(remaining,.000001)/60))
    assert np.isfinite(z).all()
    return z,remaining

def qualified(z, remaining):
    return (z>=-.5)&(remaining>0)&(remaining<=60)

def path(row, trades, source, feed, order):
    side='yes' if row['side']==1 else 'no'
    buys=[]
    for tr in trades:
        t=stamp(tr)
        assert tr.get('taker_side') in ['yes','no']
        if tr.get('taker_outcome_side') is not None:
            assert tr['taker_outcome_side']==tr['taker_side']
        if not row['entered']<t<row['close'] or tr['taker_side']!=side:
            continue
        assert not tr.get('is_block_trade',False)
        assert D(tr['yes_price_dollars'])+D(tr['no_price_dollars'])==1
        buys.append((t,tr['trade_id'],float(D(tr[side+'_price_dollars'])),float(D(tr['count_fp']))))
    buys.sort()
    tt=np.array([t[0] for t in buys])
    sz,sr=z_at(row,tt+feed,source,feed)
    fz,fr=z_at(row,tt-order,source,feed)
    return dict(t=tt,ids=[t[1] for t in buys],p=np.array([t[2] for t in buys]),
                q=np.array([t[3] for t in buys]),signal_z=sz,signal_remaining=sr,
                recheck_z=fz,recheck_remaining=fr)

def simulate(row, cap, p, feed, order):
    x={k:row[k] for k in ['ticker','lane','won','side','hold_net','entered','close','settled']}
    x.update(cap=cap,added=False,signals=0,extra_net=0.,fee=0.,cost=0.,cash_spent=0.,
             reason='no_qualifying_buy_print')
    signals=np.flatnonzero(qualified(p['signal_z'],p['signal_remaining'])&(p['p']<=cap/100))
    fills=np.flatnonzero(qualified(p['recheck_z'],p['recheck_remaining'])&(p['p']<=cap/100)&
                         (p['q']>=5)&(row['close']-p['t']>0)&(row['close']-p['t']<=60))
    ft=p['t'][fills]
    for i in signals:
        x['signals']+=1
        decision=float(p['t'][i]+feed)
        first=decision+order
        j=int(np.searchsorted(ft,first,side='left'))
        if j>=len(fills) or ft[j]>first+5:
            continue
        j=int(fills[j])
        assert p['ids'][j]!=p['ids'][i]
        costs=cash(cap)
        x.update(costs,added=True,extra_net=5*int(row['won'])-costs['cash_spent'],
                 signal_trade=p['ids'][i],execution_trade=p['ids'][j],
                 signal_print_time=float(p['t'][i]),signal_time=decision,
                 fill_time=float(p['t'][j]),signal_z=float(p['signal_z'][i]),
                 recheck_z=float(p['recheck_z'][j]),
                 execution_print_price=float(p['p'][j]),execution_print_qty=float(p['q'][j]),
                 seconds_remaining=float(row['close']-p['t'][j]),
                 signal_to_witness=float(p['t'][j]-decision),reason='subsequent_buy_print_proxy')
        return x
    if len(signals):
        x['reason']='no_subsequent_five_contract_witness_within_deadline'
    return x

def metrics(rows):
    adds=[r for r in rows if r['added']]
    wins=[r for r in adds if r['won']]
    losses=[r for r in adds if not r['won']]
    gain=sum(r['extra_net'] for r in wins)
    loss=-sum(r['extra_net'] for r in losses)
    extra=gain-loss
    hold=sum(r['hold_net'] for r in rows)
    return dict(trades=len(rows),adds=len(adds),add_wins=len(wins),add_losses=len(losses),
                extra_winner_profit=gain,extra_losses=loss,extra_net=extra,
                hold_net=hold,total_net=hold+extra,fees=sum(r['fee'] for r in adds),
                extra_net_friction1c=extra-.05*len(adds),
                total_net_friction1c=hold+extra-.05*len(adds),
                total_net_friction2c=hold+extra-.10*len(adds),
                qualifying_markets=sum(r['signals']>0 for r in rows),
                missed_proxy_additions=sum(r['signals']>0 and not r['added'] for r in rows),
                min_seconds_remaining=min((r['seconds_remaining'] for r in adds),default=None),
                additions_under5s=sum(r['seconds_remaining']<5 for r in adds),
                cash_turnover=sum(r['cash_spent'] for r in adds))

def details(rows):
    daily={}
    for d in range(7):
        day=dt.date(2026,10,8)+dt.timedelta(days=d)
        daily[str(day)]=metrics([r for r in rows if dt.datetime.fromtimestamp(r['close'],ET).date()==day])
    split=dt.datetime(2026,10,12,tzinfo=ET).timestamp()
    # Incremental risk only. The actual all-asset portfolio is NOT replayed.
    events=[]
    for r in rows:
        if r['added']:
            events.extend([(r['fill_time'],r['cash_spent'],5),(r['settled'],-r['cash_spent'],-5)])
    cash_open=0.;q=0;peak_cash=0.;peak_q=0
    for _,dc,dq in sorted(events):
        cash_open+=dc;q+=dq;peak_cash=max(peak_cash,cash_open);peak_q=max(peak_q,q)
    def dd(changes):
        equity=peak=worst=0.
        for change in changes:
            equity+=change;peak=max(peak,equity);worst=max(worst,peak-equity)
        return worst
    ordered=sorted(rows,key=lambda r:(r['settled'],r['ticker']))
    return dict(daily=daily,earlier=metrics([r for r in rows if r['close']<split]),
                later=metrics([r for r in rows if r['close']>=split]),
                lanes={lane:metrics([r for r in rows if r['lane']==lane]) for lane in sorted({r['lane'] for r in rows})},
                added_trades_per_calendar_day=sum(r['added'] for r in rows)/7,
                peak_added_cash=peak_cash,peak_added_contracts=peak_q,
                settlement_drawdown=dd([r['hold_net']+r['extra_net'] for r in ordered]),
                hold_settlement_drawdown=dd([r['hold_net'] for r in ordered]))

def main():
    data=pickle.load(gzip.open(B/'inputs.pkl.gz','rb'))
    rows=data['actual'];source=pickle.load(gzip.open(B/'seconds.pkl.gz','rb'))['WTI']
    trades=json.load(gzip.open(P/'trades.json.gz'))
    assert len(rows)==111 and sum(r['won'] for r in rows)==106
    assert set(trades)=={r['ticker'] for r in rows}
    assert all(r['quantity']==5 for r in rows)
    series=json.load(gzip.open(P/'public/series.json.gz'))['response']['series']
    assert series['fee_type']=='quadratic' and series['fee_multiplier']==1
    assert all(data['markets'][r['ticker']].get('multiplier',series['fee_multiplier'])==1 for r in rows)
    report={};ledgers={};extras={}
    for name,(feed,order) in SCENARIOS.items():
        paths={r['ticker']:path(r,trades[r['ticker']],source,feed,order) for r in rows}
        report[name]={};ledgers[name]={};extras[name]={}
        for cap in range(50,85):
            xs=[simulate(r,cap,paths[r['ticker']],feed,order) for r in rows]
            report[name][str(cap)]=metrics(xs)
            ledgers[name][str(cap)]=xs
            extras[name][str(cap)]=details(xs)
    old=json.loads((OLD/'grid_results.json').read_text())['cap50_z_ge_-0.5_last60']['full']
    regression={}
    for key in ['adds','add_wins','add_losses','extra_net','total_net']:
        regression[key]=dict(previous=old[key],current=report['feed1_order1']['50'][key])
        assert abs(old[key]-report['feed1_order1']['50'][key])<1e-8,(key,regression[key])
    save('results.json',report);save('details.json',extras);save('regression50.json',regression)
    with gzip.open(P/'ledgers.json.gz','wt') as h:
        json.dump(ledgers,h)
    save('manifest.json',dict(cohort=111,wins=106,losses=5,price_caps=list(range(50,85)),
         scenarios=SCENARIOS,simulations=35*len(SCENARIOS),
         input_hashes={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in
          [B/'inputs.pkl.gz',B/'seconds.pkl.gz',B/'frozen_orders.json',P/'trades.json.gz',P/'PLAN.txt',P/'analyze.py']}))
    for name in SCENARIOS:
        print(name)
        for cap,m in report[name].items():
            print(cap, f"{m['add_wins']}W/{m['add_losses']}L",'gain',round(m['extra_winner_profit'],4),
                  'loss',round(m['extra_losses'],4),'extra',round(m['extra_net'],4),
                  'total',round(m['total_net'],4),flush=True)

if __name__=='__main__':
    main()
