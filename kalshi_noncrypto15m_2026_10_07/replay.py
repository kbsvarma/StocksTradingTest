"""Offline full-input commodity transfer. No network calls or real orders.

Prediction quotes are execution proxies, not historical depth or actual fills.
Production math is imported read-only; production files are never edited.
"""
from collections import Counter, defaultdict
from decimal import Decimal as D, ROUND_CEILING, ROUND_HALF_UP
import datetime as dt
import gzip
import hashlib
import json
import math
from pathlib import Path
import pickle
import random
import statistics
import sys
from zoneinfo import ZoneInfo

P = Path(__file__).resolve().parent
ROOT = P.parent
sys.path.insert(0, str(P/'source'))
from lanes import LANES, Z2, Z3
from engine import limit_price
from pull import ASSETS, DATA, START, END, ts

SPLIT = ts('2026-10-03T00:00:00Z')
ET = ZoneInfo('America/New_York')
ASSET_ORDER = ('BTC', 'ETH', 'SOL', 'XRP') + ASSETS

def save(name, obj):
    (P/name).write_text(json.dumps(obj, indent=2, allow_nan=False)+'\n')

def utcday(t):
    return dt.datetime.fromtimestamp(t, dt.timezone.utc).date().isoformat()

def etday(t):
    return dt.datetime.fromtimestamp(t, ET).date().isoformat()

def fees(price, quantity=5, precision='.0001', multiplier=1):
    price, quantity = D(str(price)), D(str(quantity))
    cost = price*quantity
    raw = D('.07')*D(str(multiplier))*cost*(1-price)
    # API docs: model fee first rounds UP at 6dp, then debit aligns to balance.
    trade = raw.quantize(D('.000001'), rounding=ROUND_CEILING)
    unit = D(precision)
    debit = ((cost+trade)/unit).to_integral_value(rounding=ROUND_CEILING)*unit
    return float(debit-cost)

def quote(c, field='c', side=1):
    try:
        b, a = float(c['b'+field]), float(c['a'+field])
    except (KeyError, TypeError, ValueError):
        return None
    if not (math.isfinite(b) and math.isfinite(a) and 0 <= b <= a <= 1):
        return None
    return (b, a) if side == 1 else (1-a, 1-b)

def feature(bars, start, T, rem, side):
    keys = list(range(T-61*60, T, 60))
    if start-60 not in bars or not all(t in bars for t in keys):
        return None
    values = [bars[t] for t in keys]
    sigma = statistics.pstdev(math.log(y/x) for x, y in zip(values, values[1:]))
    if sigma <= 0:
        return None
    ref, spot = bars[start-60], bars[T-60]
    return dict(z=side*(spot/ref-1)/(sigma*math.sqrt(rem)),
                sigma=sigma, ref=ref, spot=spot)

def load_data():
    bars = {a: {} for a in ASSETS}
    quality = {a: Counter() for a in ASSETS}
    conflicts = []
    for path in sorted((DATA/'charts').glob('*.json.gz')):
        with gzip.open(path, 'rt') as f:
            raw = json.load(f)
        d = raw['response']['live_data']['details']
        asset = d['asset']
        q = quality[asset]
        q['charts'] += 1
        seen = set()
        for point in d['timeseries']:
            t, price = int(point['t']), float(point['v'])
            if t in seen:
                q['duplicate_seconds_inside_chart'] += 1
            seen.add(t)
            if not math.isfinite(price) or price <= 0:
                q['invalid_prices'] += 1
                continue
            q['seconds'] += 1
            # Exact terminal second only; never fill missing minute endpoints.
            if t % 60000 != 59000:
                continue
            minute = t//60000*60
            if minute in bars[asset] and bars[asset][minute] != price:
                conflicts.append(dict(asset=asset, minute=minute, old=bars[asset][minute], new=price))
            bars[asset][minute] = price
        q['unique_seconds'] += len(seen)
    if conflicts:
        save('chart_conflicts.json', conflicts)
        raise ValueError('conflicting historical underlying values')
    markets = []
    integrity = []
    for asset in ASSETS:
        q = quality[asset]
        q['underlying_minutes'] = len(bars[asset])
        meta = json.loads((DATA/f'markets_{asset}.json').read_text())
        candles = {r['ticker']: {int(c['t']): c for c in r['candles']}
                   for r in map(json.loads, (DATA/f'candles_{asset}.jsonl').read_text().splitlines())}
        series = json.loads((DATA/f'series_{asset}.json').read_text())['response']['series']
        assert series['fee_type'] == 'quadratic'
        for raw in meta:
            close, start = ts(raw['close_time']), ts(raw['open_time'])
            if not START <= close < END:
                continue
            q['markets'] += 1
            if close-start != 900 or float(raw.get('notional_value_dollars', 0)) != 1:
                q['unsupported_contract'] += 1
                continue
            if raw.get('result') not in ('yes', 'no'):
                q['unresolved'] += 1
                continue
            if not raw.get('settlement_ts'):
                q['missing_settlement_timestamp'] += 1
                continue
            m = dict(ticker=raw['ticker'], asset=asset, start=start, close=close,
                     settled=ts(raw['settlement_ts']), result=1 if raw['result']=='yes' else -1,
                     ranges=raw['price_ranges'], candles=candles.get(raw['ticker'], {}),
                     multiplier=float(series['fee_multiplier']), exchange_index=raw['exchange_index'],
                     features=[], quote_features=[])
            assert m['settled'] >= close
            ref, final = bars[asset].get(start-60), bars[asset].get(close-60)
            digits = int(raw.get('custom_strike', {}).get('round_digits', '2'))
            quantum = D(1).scaleb(-digits)
            def rounded(value):
                try:
                    return D(str(value).replace(',', '')).quantize(quantum, rounding=ROUND_HALF_UP)
                except Exception:
                    return None
            check = dict(ticker=m['ticker'], ref_available=ref is not None, final_available=final is not None)
            if ref is not None and rounded(raw.get('floor_strike')) is not None:
                check['reference_matches'] = rounded(ref) == rounded(raw['floor_strike'])
            else:
                q['reference_comparison_unavailable'] += 1
            if final is not None and rounded(raw.get('expiration_value')) is not None:
                check['final_matches'] = rounded(final) == rounded(raw['expiration_value'])
            else:
                q['final_comparison_unavailable'] += 1
            if ref is not None and final is not None:
                check['outcome_matches'] = (1 if rounded(final) >= rounded(ref) else -1) == m['result']
            integrity.append(check)
            for rem in range(10, 2, -1):
                T = close-rem*60
                q['decision_minutes'] += 1
                pair = quote(m['candles'].get(T))
                if pair is None:
                    q['missing_quote'] += 1
                    continue
                side = 1 if sum(pair)>1 else -1
                bid, ask = pair if side == 1 else (1-pair[1], 1-pair[0])
                f = dict(t=T, rem=rem, side=side, bid=bid, ask=ask)
                m['quote_features'].append(f.copy())
                extra = feature(bars[asset], start, T, rem, side)
                if extra is None:
                    q['incomplete_or_zero_volatility'] += 1
                    continue
                f.update(extra)
                m['features'].append(f)
                q['complete_feature_minutes'] += 1
            if m['features']:
                q['markets_with_features'] += 1
            markets.append(m)
    mismatches = [r for r in integrity if any(r.get(k) is False for k in ('reference_matches', 'final_matches', 'outcome_matches'))]
    save('integrity_checks.json', {'checks': len(integrity), 'mismatches': mismatches,
                                 'reference_available': sum(r['ref_available'] for r in integrity),
                                 'final_available': sum(r['final_available'] for r in integrity)})
    save('coverage.json', quality)
    (P/'features.pickle').write_bytes(pickle.dumps(markets))
    (P/'underlying_minutes.pickle').write_bytes(pickle.dumps(bars))
    return markets, bars

def qualifies(f, lane, use_z=True):
    r = LANES[lane]
    return (3 <= f['rem'] <= 10 and float(r.floor)-1e-8 <= f['ask'] < float(r.ceiling)-1e-8
            and f['ask']-f['bid'] <= .02000001 and (not use_z or f['z'] >= r.z))

def opportunities(markets, mode='opening', use_z=True, precision='.0001', common_coverage=True):
    out, counts = [], Counter()
    for m in markets:
        fs = m['features'] if use_z or common_coverage else m['quote_features']
        for f in fs:
            for lane in (Z2, Z3):
                if not qualifies(f, lane, use_z):
                    continue
                rule = LANES[lane]
                counts[lane+'_qualifying_minutes'] += 1
                s = dict(lane=lane, signal_ask=f['ask'], side='yes' if f['side']==1 else 'no', price_ranges=m['ranges'])
                cap = float(limit_price(s)[1])
                # Next minute OPEN is a price at the start of that minute, T.
                # T+3 is an assumed execution timestamp, not a measured fill.
                pair = quote(m['candles'].get(f['t']+60), 'c' if mode=='delay60' else 'o', f['side'])
                if pair is None:
                    counts['missing_execution_quote'] += 1
                    continue
                entry = pair[1]
                if entry < float(rule.floor)-1e-8 or entry > cap+1e-8:
                    counts['outside_cap_or_floor'] += 1
                    continue
                if mode == 'cap_cost':
                    entry = cap
                paid = fees(entry, 5, precision, m.get('multiplier', 1))
                won = int(f['side']==m['result'])
                out.append(dict(ticker=m['ticker'], asset=m['asset'], lane=lane, t=f['t'],
                    entered=f['t']+(60 if mode=='delay60' else 3), close=m['close'], settled=m['settled'],
                    side=f['side'], entry=entry, cap=cap, quantity=5, fee=paid, won=won,
                    pnl=5*(won-entry)-paid, ask=f['ask'], bid=f['bid'], rem=f['rem'],
                    z=f.get('z'), mode=mode))
    return out, dict(counts)

def portfolio(rows, stress=.01, start_cash=50, enforce=True):
    rows = sorted(rows, key=lambda r: (r['entered'], 0 if r['lane']==Z2 else 1, ASSET_ORDER.index(r['asset'])))
    cash = start_cash
    active, closed, equity = [], [], []
    realized = defaultdict(float)
    lane_realized = defaultdict(float)
    seen = set()
    blocked = Counter()
    def settle(t):
        nonlocal active, cash
        remain = []
        for r in sorted(active, key=lambda x: (x['settled'], x['ticker'])):
            if r['settled'] <= t:
                cash += r['won']*r['quantity']
                realized[etday(r['settled'])] += r['net']
                lane_realized[(etday(r['settled']), r['lane'])] += r['net']
                closed.append(r)
                equity.append((r['settled'], r['net']))
            else:
                remain.append(r)
        active = remain
    for r in rows:
        settle(r['entered'])
        if r['ticker'] in seen:
            continue
        reserve = float(LANES[r['lane']].reserve)*r['quantity']
        risk = sum(p['reserve'] for p in active)+reserve
        if enforce:
            if any(p['asset']==r['asset'] for p in active):
                blocked['asset'] += 1
                continue
            if sum(p['quantity'] for p in active)+r['quantity'] > 20:
                blocked['contracts'] += 1
                continue
            if risk > 19+1e-8:
                blocked['exposure'] += 1
                continue
            if realized[etday(r['entered'])]-risk < -10-1e-8:
                blocked['daily_including_open_risk'] += 1
                continue
            if r['lane'] == Z3:
                own = [p for p in active if p['lane']==Z3]
                own_count = sum(p['quantity'] for p in own)+r['quantity']
                own_risk = sum(p['reserve'] for p in own)+reserve
                if own_count>20 or own_risk>20+1e-8:
                    blocked['z3_own_exposure'] += 1
                    continue
                if lane_realized[(etday(r['entered']),Z3)]-own_risk < -25-1e-8:
                    blocked['z3_own_daily'] += 1
                    continue
            if cash < max(5, reserve)-1e-8:
                blocked['cash'] += 1
                continue
        net = r['pnl']-stress*r['quantity']
        cash -= r['quantity']*r['entry']+r['fee']+stress*r['quantity']
        active.append(dict(r, net=net, reserve=reserve))
        seen.add(r['ticker'])
    settle(float('inf'))
    return sorted(closed, key=lambda r: (r['settled'], r['ticker'])), dict(blocked)

def metrics(rows):
    daily, interval = defaultdict(float), defaultdict(float)
    for r in rows:
        daily[etday(r['settled'])] += r['net']
        interval[r['settled']] += r['net']
    eq = peak = dd = 0.
    for t in sorted(interval):
        eq += interval[t]
        peak = max(peak, eq)
        dd = max(dd, peak-eq)
    return dict(trades=len(rows), wins=sum(r['won'] for r in rows),
                losses=sum(1-r['won'] for r in rows), net=round(eq, 5),
                settled_drawdown=round(dd, 5), fee_total=round(sum(r['fee'] for r in rows), 5),
                trading_days=len(daily), daily={d: round(p, 5) for d,p in sorted(daily.items())},
                without_best_3_trades=round(eq-sum(sorted((r['net'] for r in rows), reverse=True)[:3]), 5),
                without_best_day=round(eq-max(daily.values(), default=0), 5))

def windows():
    return [('prior_period', START, SPLIT), ('fresh_oct3_7', SPLIT, END), ('pooled', START, END)]

def run_all(markets):
    result, ledgers = {}, {}
    scenarios = [('opening_fees', 'opening', 0, '.0001'),
                 ('opening_plus1c', 'opening', .01, '.0001'),
                 ('opening_plus2c', 'opening', .02, '.0001'),
                 ('delay60_plus1c', 'delay60', .01, '.0001'),
                 ('cap_cost_plus1c', 'cap_cost', .01, '.0001'),
                 ('wholecent_plus1c', 'opening', .01, '.01')]
    for scenario, mode, stress, precision in scenarios:
        primary, counts = opportunities(markets, mode, True, precision)
        controls, _ = opportunities(markets, mode, False, precision)
        out = {}
        for period, start, end in windows():
            block = {}
            for asset in (*ASSETS, 'ALL'):
                select = lambda rs: [r for r in rs if start<=r['close']<end and (asset=='ALL' or r['asset']==asset)]
                rows, blocked = portfolio(select(primary), stress)
                ctrl, ctrlblocks = portfolio(select(controls), stress)
                unc, _ = portfolio(select(primary), stress, enforce=False)
                one = metrics(rows)
                one.update(blocked=blocked, quote_only_same_coverage=metrics(ctrl),
                           quote_only_blocked=ctrlblocks, unconstrained=metrics(unc),
                           lanes={lane: metrics([r for r in rows if r['lane']==lane]) for lane in (Z2,Z3)})
                block[asset] = one
                ledgers[f'{scenario}/{period}/{asset}'] = rows
            out[period] = block
        result[scenario] = dict(signal_counts=counts, periods=out)
        print(scenario, {p: {a: (v['trades'],v['wins'],v['losses'],v['net']) for a,v in b.items()}
                         for p,b in out.items()}, flush=True)
    save('results.json', result)
    with gzip.open(P/'trade_ledgers.json.gz', 'wt') as f:
        json.dump(ledgers, f, separators=(',', ':'))
    return result, ledgers

def crypto_interaction(markets):
    # Reconstruct all opportunities, not only previously selected filled trades.
    crypto = pickle.loads((ROOT/'kalshi_btc_eth_quant_2026_10_02/features.pickle').read_bytes())
    rows = []
    for m in crypto:
        if not START <= m['close'] < END:
            continue
        m = dict(m, multiplier=1)
        rows.append(m)
    result = {}
    for mode, stress in [('opening', .01), ('opening', .02), ('delay60', .01)]:
        cr, _ = opportunities(rows, mode)
        cm, _ = opportunities(markets, mode)
        out = {}
        for period, start, end in windows():
            base, _ = portfolio([r for r in cr if start<=r['close']<end], stress)
            ids = {r['ticker'] for r in base}
            pairs = {}
            for asset in (*ASSETS, 'ALL'):
                extra = [r for r in cm if start<=r['close']<end and (asset=='ALL' or r['asset']==asset)]
                joint, blocks = portfolio([r for r in cr if start<=r['close']<end]+extra, stress)
                kept = [r for r in joint if r['asset'] not in ASSETS]
                new = [r for r in joint if r['asset'] in ASSETS]
                pairs[asset] = dict(joint=metrics(joint), commodity_attribution=metrics(new),
                    displaced_crypto=len(ids-{r['ticker'] for r in kept}), blocked=blocks,
                    incremental_net=round(metrics(joint)['net']-metrics(base)['net'], 5))
            out[period] = dict(crypto_alone=metrics(base), with_commodities=pairs)
        result[f'{mode}/{stress}'] = out
    save('crypto_interaction.json', result)
    return result

def main():
    receipt = json.loads((DATA/'pull_receipt.json').read_text())
    assert not receipt['errors'], receipt['errors']
    markets, bars = load_data()
    print('loaded', len(markets), 'markets', flush=True)
    run_all(markets)
    # Recent crypto features are not present in the frozen old cache. Never
    # compare a 27-day commodity simulation with only 22 days of crypto data.
    hashes = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in sorted(P.rglob('*')) if p.is_file() and p.suffix in ('.py', '.json', '.jsonl', '.gz', '.pickle')
              and p.name != 'manifest.json'}
    save('manifest.json', {'created_utc': dt.datetime.now(dt.timezone.utc).isoformat(),
                          'sha256': hashes, 'live_changes': False,
                          'execution': 'Quote proxies only; historical five-contract depth is not established.'})

if __name__ == '__main__':
    main()
