"""Offline shared-budget comparison. Imports no credentials or order client."""
import hashlib
import json
import pickle
import sys
import tempfile
from collections import Counter, defaultdict
from datetime import datetime
from decimal import Decimal as D
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

P = Path(__file__).resolve().parent
ROOT = P.parent
sys.path.insert(0, str(P / 'source'))
import engine
import lanes

ET = ZoneInfo('America/New_York')
NAMES = ('cushion_z2', 'premium_z3')
etday = lambda t: datetime.fromtimestamp(t, ET).date().isoformat()


class MemoryStore:
    def __init__(self, records=()):
        self.rows = {r['ticker']: dict(r) for r in records}
    def get(self, ticker):
        return self.rows.get(ticker)
    def all(self):
        return list(self.rows.values())
    def save(self, record):
        self.rows[record['ticker']] = dict(record)


class NoTransport:
    def __getattr__(self, name):
        raise AssertionError(f'Offline replay attempted exchange access: {name}')


def replay(records, budget, stress='.01', initial_cash='50', seed=(), settlement_delay=0):
    """Current production entry_guard; only its in-memory budget is changed."""
    active, closed = [], []
    blocked, denied = Counter(), []
    cash = D(str(initial_cash))
    stress = D(str(stress))
    store = MemoryStore(seed)
    now = [0.]
    max_open_risk = D(0)
    max_open_contracts = 0
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / lanes.Z3_ARM).write_text('5')
        bot = engine.Engine(NoTransport(), store, root, live=True, clock=lambda: now[0])
        def settle(t):
            nonlocal cash, active
            left = []
            for r in active:
                if r['settled'] + settlement_delay <= t:
                    s = dict(store.get(r['ticker']), status='settled', pnl=str(r['net']),
                             settled_day=etday(r['settled'] + settlement_delay))
                    store.save(s)
                    cash += D(str(r['quantity'])) * r['won']
                    closed.append(dict(r,settled=r['settled']+settlement_delay))
                else:
                    left.append(r)
            active = left
        with patch.object(engine, 'DAILY_LOSS_BUDGET', D(str(budget))), \
                patch.object(engine, 'today', side_effect=lambda: etday(now[0])):
            for src in sorted(records, key=lambda r: (r['entered'], NAMES.index(r['lane']), r['asset'])):
                now[0] = src['entered']
                settle(now[0])
                s = dict(src, exchange_index=2, decision_ts=src.get('t',src['entered']))
                reason = bot.entry_guard(s, {}, [], cash)
                if reason:
                    blocked[reason] += 1
                    if reason != 'Already attempted this market':
                        denied.append(dict(ticker=s['ticker'], lane=s['lane'], ts=now[0], reason=reason))
                    continue
                n = D(str(s['quantity']))
                debit = D(str(s['entry'])) * n + D(str(s['fee'])) + stress * n
                net = n * s['won'] - debit
                s.update(net=net, debit=debit)
                cash -= debit
                store.save(dict(s, status='open', filled=str(n), requested_count='5'))
                active.append(s)
                reserved = sum((D(5) * lanes.LANES[r['lane']].reserve for r in active), D(0))
                qty = 5 * len(active)
                assert reserved <= D('19') and qty <= 20
                realized = sum((D(r['pnl']) for r in store.all()
                                if r.get('settled_day') == etday(now[0]) and r.get('pnl') is not None), D(0))
                assert realized - reserved >= -D(str(budget))
                max_open_risk = max(max_open_risk, reserved)
                max_open_contracts = max(max_open_contracts, qty)
            settle(float('inf'))
    return closed, dict(blocked), denied, dict(max_open_risk=float(max_open_risk),
                                             max_open_contracts=max_open_contracts,
                                             final_cash=float(cash))


def metrics(rows):
    net = sum((r['net'] for r in rows), D(0))
    grouped, daily = defaultdict(lambda: D(0)), defaultdict(lambda: D(0))
    for r in rows:
        grouped[r['settled']] += r['net']
        daily[etday(r['settled'])] += r['net']
    eq = high = dd = D(0)
    for t in sorted(grouped):
        eq += grouped[t]
        high = max(high, eq)
        dd = max(dd, high-eq)
    return dict(trades=len(rows), wins=sum(r['won'] for r in rows),
                losses=sum(1-r['won'] for r in rows), net=float(net),
                settled_drawdown=float(dd), worst_et_day=float(min(daily.values(),default=D(0))),
                lanes={lane:dict(trades=sum(r['lane']==lane for r in rows),
                    wins=sum(r['won'] for r in rows if r['lane']==lane),
                    losses=sum(1-r['won'] for r in rows if r['lane']==lane),
                    net=float(sum((r['net'] for r in rows if r['lane']==lane),D(0)))) for lane in NAMES},
                daily={k:float(v) for k,v in sorted(daily.items())})


def compare(records, stress='.01', initial_cash='50', seed=(), settlement_delay=0):
    out, chosen = {}, {}
    for budget in (10,15):
        accepted, blocks, denied, capital = replay(records, budget, stress, initial_cash, seed, settlement_delay)
        out[str(budget)] = dict(metrics(accepted), blocks=blocks, **capital)
        chosen[str(budget)] = accepted
    a,b = chosen['10'],chosen['15']
    ia,ib = {r['ticker']:r for r in a},{r['ticker']:r for r in b}
    added,lost = sorted(ib.keys()-ia.keys()),sorted(ia.keys()-ib.keys())
    out['difference'] = dict(trades=len(b)-len(a), net=out['15']['net']-out['10']['net'],
        z3_trades=out['15']['lanes']['premium_z3']['trades']-out['10']['lanes']['premium_z3']['trades'],
        drawdown=out['15']['settled_drawdown']-out['10']['settled_drawdown'],
        added=added,displaced=lost,
        added_rows=[ib[t] for t in added],displaced_rows=[ia[t] for t in lost])
    return out,chosen


def dump(path,obj):
    path.write_text(json.dumps(obj,indent=2,default=lambda x:str(x) if isinstance(x,D) else x)+'\n')


def historical():
    study = ROOT / 'kalshi_z_time_research_2026_10_03'
    scenarios = {'quote':'quote_opportunities.pickle','books25':'books25_opportunities.pickle',
                 'books_delay10':'books25_delay10_opportunities.pickle'}
    out, ledgers = {}, {}
    manifests = {}
    for scenario,file in scenarios.items():
        path = study / file
        manifests[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
        records,arr = pickle.loads(path.read_bytes())
        candidates = [dict(records[int(x[15])],lane=NAMES[int(x[3])]) for x in arr if int(x[3]) < 2]
        groups = [('all',candidates)] + [(co,[r for r in candidates if r['cohort']==co])
            for co in ('development','validation','later','october2')]
        out[scenario], ledgers[scenario] = {}, {}
        for co,rr in groups:
            if not rr: continue
            result,chosen=compare(rr)
            stress,_=compare(rr,stress='.02')
            delay,_=compare(rr,settlement_delay=60)
            result['stress2c']={k:{f:v[f] for f in ('trades','net','settled_drawdown','lanes')}
                                 for k,v in stress.items() if k in ('10','15')}
            result['settlement_plus60s']={k:{f:v[f] for f in ('trades','net','settled_drawdown','lanes')}
                                 for k,v in delay.items() if k in ('10','15')}
            out[scenario][co]=result
            ledgers[scenario][co]=chosen
            print(scenario,co,[(k,result[k]['trades'],round(result[k]['net'],4),
                  result[k]['lanes']['premium_z3']['trades'],round(result[k]['settled_drawdown'],4))
                  for k in ('10','15')],flush=True)
    control=out['quote']['later']['10']
    assert control['trades']==302 and abs(control['net']-65.9172)<1e-7,control
    dump(P/'historical_results.json',out)
    dump(P/'historical_ledgers.json',ledgers)
    dump(P/'historical_input_hashes.json',manifests)


if __name__=='__main__':
    historical()
