"""Independent arithmetic, causal data and unchanged production risk checks."""
from collections import Counter
from decimal import Decimal as D
import math
import pickle
import tempfile
from unittest.mock import patch
import numpy as np
import study as s
import engine
from lanes import Z3_ARM

def guard_replay(rows,stress=.01):
    # Engine receives offline signals and temporary SQLite only. Never creates
    # an Exchange/client, account transport or real order.
    now=[0];cash=D('50');active=[];done=[];blocks=Counter()
    with tempfile.TemporaryDirectory(prefix='noncrypto-tuned-risk-') as td:
        root=s.Path(td);(root/Z3_ARM).write_text('5')
        store=engine.Store(root/'offline.sqlite3')
        bot=engine.Engine(object(),store,root,live=True,clock=lambda:now[0])
        def settle(t):
            nonlocal active,cash
            left=[]
            for row in sorted(active,key=lambda x:(x['settled'],x['ticker'])):
                if row['settled']<=t:
                    item=store.get(row['ticker'])
                    item.update(status='settled',pnl=str(round(row['net'],8)),settled_day=s.r.etday(row['settled']))
                    store.save(item);cash+=D(row['won']*5);done.append(row)
                else:left.append(row)
            active=left
        with patch.object(engine,'today',side_effect=lambda:s.r.etday(now[0])):
            for row in sorted(rows,key=lambda x:(x['entered'],0 if x['lane']==s.r.Z2 else 1,s.r.ASSET_ORDER.index(x['asset']))):
                now[0]=row['entered'];settle(now[0])
                signal=dict(row,exchange_index=2,decision_ts=row['t'],close_ts=row['close']) # offline risk math, not route proof
                reason=bot.entry_guard(signal,{},[],cash)
                if reason:blocks[reason]+=1;continue
                store.save(dict(signal,status='open',filled='5',requested_count='5'))
                cash-=D(str(round(5*row['entry']+row['fee']+stress*5,8)))
                active.append(dict(row,net=row['pnl']-stress*5))
        settle(float('inf'));store.db.close()
    return sorted(done,key=lambda x:(x['settled'],x['ticker'])),dict(blocks)

def compare(rows,stress=.01):
    expected,_=s.r.portfolio(rows,stress);actual,blocks=guard_replay(rows,stress)
    key=lambda x:(x['ticker'],x['lane'],x['t'],round(x['entry'],10))
    assert [key(x) for x in expected]==[key(x) for x in actual]
    assert abs(sum(x['net'] for x in expected)-sum(x['net'] for x in actual))<1e-7
    return dict(same_entries=True,metrics=s.compact(actual),blocked=blocks)

def main():
    markets=pickle.loads((s.P/'features.pickle').read_bytes())
    bars=pickle.loads((s.OLD/'underlying_minutes.pickle').read_bytes())
    count=0;maxdiff=0;future=0;missing=0
    for m in markets:
        for f in m['features']:
            if f['rem'] in range(3,11) and f['t']//60%97:continue
            vals=np.array([bars[m['asset']][k] for k in range(f['t']-3660,f['t'],60)])
            sigma=float(np.std(np.log(vals[1:]/vals[:-1]),ddof=0))
            z=f['side']*(vals[-1]/bars[m['asset']][m['start']-60]-1)/(sigma*math.sqrt(f['rem']))
            maxdiff=max(maxdiff,abs(z-f['z']));assert abs(z-f['z'])<1e-8
            count+=1
        if future<5 and m['features']:
            f=m['features'][0];bb=dict(bars[m['asset']]);T=f['t']
            before=s.r.feature(bb,m['start'],T,f['rem'],f['side'])
            bb[T]=1e15;bb[T+60]=1e-15
            assert s.r.feature(bb,m['start'],T,f['rem'],f['side'])==before;future+=1
            bb.pop(T-1800)
            assert s.r.feature(bb,m['start'],T,f['rem'],f['side']) is None;missing+=1
    rows=s.pool(markets);index={m['ticker']:m for m in markets};ticks=0
    for row in rows:
        if row['ask']<float(s.r.LANES[row['lane']].floor):continue
        sig=dict(row,signal_ask=row['ask'],side='yes' if row['side']==1 else 'no',price_ranges=index[row['ticker']]['ranges'])
        try:cap=float(s.r.limit_price(sig)[1])
        except ValueError:continue
        assert abs(cap-row['cap'])<1e-10;ticks+=1
    # Signal boundary fixtures include disabled lane, late window and price floor.
    c=dict(s.CONFIG[0],lo=1,hi=5,z2=1.75,z3=None)
    exemplar=next(x for x in rows if x['lane']==s.r.Z2)
    fixture=[dict(exemplar,rem=1,z=1.75,ask=.85,observed_entry=.85),
             dict(exemplar,rem=5,z=1.74999,ask=.90,observed_entry=.90),
             dict(exemplar,rem=6,z=9,ask=.90,observed_entry=.90),
             dict(exemplar,rem=3,z=9,ask=.849,observed_entry=.90),
             dict(exemplar,rem=3,z=9,ask=.90,observed_entry=.849),
             dict(exemplar,lane=s.r.Z3,rem=3,z=9,ask=.95,observed_entry=.95)]
    assert s.select(fixture,c)==fixture[:1]
    choices=s.json.loads((s.P/'choices_frozen.json').read_text())['choices'];guard={}
    for a in s.ASSETS:
        for i in sorted({0,choices[a]['robust']}):
            aa=s.select([x for x in rows if x['asset']==a],s.CONFIG[i])
            guard[f'{a}/{i}']=compare(aa)
            print('GUARD PASS',a,i,guard[f'{a}/{i}']['metrics']['trades'],flush=True)
    s.save('validation.json',dict(status='PASS',independent_z_checks=count,max_difference=maxdiff,
        future_prices_ignored=future,missing_history_rejected=missing,tick_cap_parities=ticks,
        inclusive_z_time_and_price_boundaries=True,disabled_lane_rejects=True,
        production_risk_guard=guard,
        adapter_scope='Research signal thresholds/times differ. Original entry_guard only; mocked exchange index2 for mathematical parity, no live FX routing proof.'))

if __name__=='__main__':main()
