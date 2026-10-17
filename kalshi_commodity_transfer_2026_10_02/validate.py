"""Checks against the unchanged production scanner/engine using offline adapters."""
from collections import Counter
from decimal import Decimal as D
import json
from pathlib import Path
import pickle
import tempfile
from unittest.mock import patch
import replay as r
import engine
import market_data
from lanes import Z2, Z3, Z3_ARM

def scanner(markets, bars):
    checked, passes, max_error = Counter(), Counter(), 0.
    for m in markets:
        for f in m['features']:
            for lane in (Z2, Z3):
                expected = r.qualifies(f, lane)
                if not expected and (f['t']//60)%97:
                    continue
                T = f['t']
                iso = lambda t: r.dt.datetime.fromtimestamp(t, r.dt.timezone.utc).isoformat()
                metadata = dict(ticker=m['ticker'], open_time=iso(m['start']), close_time=iso(m['close']),
                                exchange_index=2, notional_value_dollars='1', price_ranges=m['ranges'])
                raw = [[k,0,0,0,bars[m['asset']][k],0]
                       for k in range(T-61*60,T,60) if k in bars[m['asset']]]
                raw.append([T,0,0,0,1e12,0])  # future/forming value must not leak
                bid, ask = r.quote(m['candles'][T])
                cap = float(engine.limit_price(dict(lane=lane, side='yes' if f['side']==1 else 'no',
                                  signal_ask=f['ask'], price_ranges=m['ranges']))[1]) if expected else .90
                def fake_get(url):
                    if '/orderbook' in url:
                        return {'orderbook_fp': {'no_dollars' if f['side']==1 else 'yes_dollars': [[str(1-D(str(cap))), '5']]}}
                    if 'coinbase' in url:
                        return raw
                    if '/candlesticks?' in url:
                        return {'candlesticks': [dict(end_period_ts=T, yes_bid={'close_dollars': str(bid)},
                                                    yes_ask={'close_dollars': str(ask)})]}
                    return {'markets': [metadata]}
                with patch.object(market_data, 'get', side_effect=fake_get), patch.object(market_data.time, 'time', return_value=T+3):
                    got = market_data.scan(m['asset'], T, set(), lane=lane)
                assert (got['status']=='paper_fill') == expected, (m['ticker'], T, lane, got, expected)
                checked[lane] += 1
                if expected:
                    delta = abs(got['z']-f['z'])
                    assert delta < 1e-10
                    max_error = max(max_error, delta)
                    passes[lane] += 1
    return dict(decisions=dict(checked), qualifying=dict(passes), max_z_difference=max_error,
                forming_bars_ignored=True)

class OfflineExchange:
    def __init__(self):
        self.cash = D('50')
        self.row = None
    def positions(self):
        return []
    def orders(self, **kwargs):
        return []
    def balance(self):
        return self.cash
    def book(self, ticker):
        row = self.row
        # Exchange book prices are decimal strings, not a binary-float inverse.
        return {'no_dollars' if row['side']==1 else 'yes_dollars': [[str(1-D(str(round(row['entry'],10)))), '5']]}
    def submit(self, record):
        return {'order_id': 'OFFLINE-'+record['client_id']}

def actual_engine(rows, index, stress=.01):
    now = [0]
    ex = OfflineExchange()
    active, closed = [], []
    blocks = Counter()
    with tempfile.TemporaryDirectory(prefix='commodity-replay-') as tmp:
        root = Path(tmp)
        (root/Z3_ARM).write_text('5')
        store = engine.Store(root/'offline.sqlite3')
        bot = engine.Engine(ex, store, root, live=True, clock=lambda: now[0])
        # live=True exercises the original state machine, but the exchange above
        # has no network transport and all state is in this temporary directory.
        def settle(T):
            nonlocal active
            remain = []
            for row in sorted(active, key=lambda v:(v['settled'],v['ticker'])):
                if row['settled'] <= T:
                    item = store.get(row['ticker'])
                    item.update(status='settled', pnl=str(round(row['net'],8)), settled_day=r.etday(row['settled']))
                    store.save(item)
                    ex.cash += D(row['won']*5)
                    closed.append(row)
                else:
                    remain.append(row)
            active = remain
        series = tuple('KX'+a+'15M' for a in r.ASSET_ORDER)
        with patch.object(engine, 'SERIES', series), patch.object(engine, 'today', side_effect=lambda:r.etday(now[0])):
            for row in sorted(rows, key=lambda x:(x['entered'],0 if x['lane']==Z2 else 1,r.ASSET_ORDER.index(x['asset']))):
                now[0] = row['entered']
                settle(now[0])
                ex.row = row
                m = index[row['ticker']]
                signal = dict(status='paper_fill', ticker=row['ticker'], asset=row['asset'], lane=row['lane'],
                    signal_ask=row['ask'], signal_bid=row['bid'], side='yes' if row['side']==1 else 'no',
                    z=row['z'], remaining_minutes=row['rem'], decision_ts=row['t'], close_ts=row['close'],
                    exchange_index=2, price_ranges=m['ranges'])
                reason = bot.attempt(signal)
                if reason != 'ack':
                    blocks[reason] += 1
                    continue
                record = store.get(row['ticker'])
                record.update(status='open', filled='5')
                assert record['requested_count']=='5'
                store.save(record)
                net = row['pnl']-stress*5
                ex.cash -= D(str(round(5*row['entry']+row['fee']+stress*5,8)))
                active.append(dict(row, net=net, reserve=float(r.LANES[row['lane']].reserve)*5))
        settle(float('inf'))
        store.db.close()
    return sorted(closed, key=lambda x:(x['settled'],x['ticker'])), dict(blocks)

def risk_parity(markets):
    rows, _ = r.opportunities(markets)
    index = {m['ticker']:m for m in markets}
    result = {}
    for asset in (*r.ASSETS, 'ALL'):
        subset = [row for row in rows if asset=='ALL' or row['asset']==asset]
        expected, _ = r.portfolio(subset)
        got, blocked = actual_engine(subset, index)
        key = lambda row:(row['ticker'],row['lane'],row['t'],round(row['entry'],8))
        assert [key(x) for x in expected] == [key(x) for x in got], asset
        assert abs(sum(x['net'] for x in expected)-sum(x['net'] for x in got)) < 1e-7
        result[asset] = dict(trades=len(got), net=round(sum(x['net'] for x in got),5), same_entries=True, blocked=blocked)
    return result

def combined_risk_parity(markets):
    crypto = pickle.loads((r.ROOT/'kalshi_btc_eth_quant_2026_10_02/features.pickle').read_bytes())
    combined = [dict(m,multiplier=1) for m in crypto if r.START<=m['close']<r.END]
    combined += [m for m in markets if m['asset'] in ('GOLD','COPPER')]
    rows,_ = r.opportunities(combined)
    expected,_ = r.portfolio(rows)
    got,blocked = actual_engine(rows,{m['ticker']:m for m in combined})
    key = lambda row:(row['ticker'],row['lane'],row['t'],round(row['entry'],8))
    assert [key(x) for x in expected]==[key(x) for x in got]
    assert abs(sum(x['net'] for x in expected)-sum(x['net'] for x in got))<1e-7
    return dict(trades=len(got),net=round(sum(x['net'] for x in got),5),same_entries=True,blocked=blocked)

def crypto_regression():
    raw = pickle.loads((r.ROOT/'kalshi_btc_eth_quant_2026_10_02/features.pickle').read_bytes())
    start, end = r.ts('2026-09-12T00:00:00Z'), r.ts('2026-10-02T00:00:00Z')
    raw = [dict(m,multiplier=1) for m in raw if start<=m['close']<end]
    rows, _ = r.opportunities(raw)
    got, _ = r.portfolio(rows)
    result = r.metrics(got)
    assert result['trades']==302, result
    assert abs(result['net']-65.9172)<.0001, result
    return {k:result[k] for k in ('trades','wins','losses','net')}

def causality(markets, bars):
    m = next(m for m in markets if m['features'])
    f = m['features'][0]
    bb = dict(bars[m['asset']])
    before = r.feature(bb,m['start'],f['t'],f['rem'],f['side'])
    bb[f['t']] = 1e20
    bb[f['t']+60] = 1e-20
    assert r.feature(bb,m['start'],f['t'],f['rem'],f['side'])==before
    bb.pop(f['t']-31*60)
    assert r.feature(bb,m['start'],f['t'],f['rem'],f['side']) is None
    assert r.fees(.95,5)==.0167
    assert r.fees(.95,5,'.01')==.02
    assert r.ts('2026-09-11T10:30:23.69909Z')==r.ts('2026-09-11T10:30:23.699090Z')
    return {'future_prices_ignored': True, 'missing_minute_rejects': True,
            'five_contract_fee_rounding': True, 'fractional_timestamp_parser': True}

def main():
    markets = pickle.loads((r.P/'features.pickle').read_bytes())
    bars = pickle.loads((r.P/'underlying_minutes.pickle').read_bytes())
    out = {'causality': causality(markets,bars), 'crypto_control': crypto_regression()}
    print('causality and crypto control PASS', flush=True)
    out['scanner'] = scanner(markets,bars)
    print('scanner PASS', out['scanner'], flush=True)
    out['production_risk_engine'] = risk_parity(markets)
    out['crypto_plus_gold_copper_risk_engine'] = combined_risk_parity(markets)
    out['status'] = 'PASS'
    r.save('validation.json',out)
    print('production risk engine PASS', flush=True)

if __name__=='__main__':
    main()
