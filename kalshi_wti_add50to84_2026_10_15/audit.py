"""Independent raw receipt, scalar first-add, source, and Decimal cash audit."""
import bisect, datetime as dt, gzip, hashlib, json, math, pickle
from decimal import Decimal as D, ROUND_CEILING
from pathlib import Path

P=Path(__file__).resolve().parent
B=P.parent/'kalshi_wti_sell_floors_2026_10_15'
data=pickle.load(gzip.open(B/'inputs.pkl.gz','rb'))
rows={r['ticker']:r for r in data['actual']}
start,prices=pickle.load(gzip.open(B/'seconds.pkl.gz','rb'))['WTI']
orders={r['ticker']:r for r in json.loads((B/'frozen_orders.json').read_text())['orders']}
trades=json.load(gzip.open(P/'trades.json.gz'))
ledgers=json.load(gzip.open(P/'ledgers.json.gz'))
results=json.loads((P/'results.json').read_text())
manifest=json.loads((P/'manifest.json').read_text())

def stamp(t):
    s=t['created_time'].replace('Z','+00:00')
    return (dt.datetime.strptime(s,'%Y-%m-%dT%H:%M:%S.%f%z')
            if '.' in s else dt.datetime.fromisoformat(s)).timestamp()

def zvalue(r,at,feed):
    rem=r['close']-at
    assert rem>0
    i=math.floor(at-feed)-start
    assert 0<=i<len(prices)
    spot=float(prices[i])
    assert math.isfinite(spot)
    return r['side']*((spot-r['reference'])/r['reference'])/(r['entry_sigma']*math.sqrt(rem/60))

def qualify(r,at,feed):
    return 0<r['close']-at<=60 and zvalue(r,at,feed)>=-.5

capture=json.loads((P/'capture_audit.json').read_text())
raw={};groups={}
for receipt in capture['receipts']:
    p=P/'public'/receipt['file']
    assert hashlib.sha256(p.read_bytes()).hexdigest()==receipt['hash']
    payload=json.load(gzip.open(p))
    pars=payload['params'];ticker=pars['ticker'];r=rows[ticker]
    assert pars['min_ts'] in [r['close']-65,r['close']-62]
    assert pars['max_ts'] in [r['close']-62,r['close']]
    groups.setdefault((ticker,pars['min_ts'],pars['max_ts']),[]).append(payload)
    for t in payload['response']['trades']:
        assert t['ticker']==ticker and not t.get('is_block_trade',False)
        assert pars['min_ts']<=stamp(t)<pars['max_ts']+1
        if t['trade_id'] in raw:
            assert raw[t['trade_id']]==t
        raw[t['trade_id']]=t
assert len(groups)==222
for key,pg in groups.items():
    pg.sort(key=lambda p:bool(p['params'].get('cursor')))
    bycursor={p['params'].get('cursor',''):p for p in pg}
    cursor='';visited=set()
    while True:
        assert cursor not in visited
        visited.add(cursor)
        cursor=bycursor[cursor]['response'].get('cursor','')
        if not cursor:break
    assert len(visited)==len(pg)
byid={t['trade_id']:t for ts in trades.values() for t in ts}
assert raw==byid and len(raw)==capture['trades']
assert set(trades)==set(rows) and len(rows)==111
assert sum(D(o['pnl']) for o in orders.values())==D('15.3050')
series=json.load(gzip.open(P/'public/series.json.gz'))['response']['series']
assert series['fee_type']=='quadratic' and series['fee_multiplier']==1

buys={}
for ticker,ts in trades.items():
    r=rows[ticker];side=orders[ticker]['side']
    assert side==('yes' if r['side']==1 else 'no')
    buys[ticker]=sorted((stamp(t),t['trade_id'],D(t[side+'_price_dollars']),D(t['count_fp']))
                        for t in ts if t['taker_side']==side and r['entered']<stamp(t)<r['close'])

checks=cashchecks=witnesses=0
for scenario,caps in ledgers.items():
    feed,order=manifest['scenarios'][scenario]
    assert set(caps)=={str(i) for i in range(50,85)}
    for capstr,xs in caps.items():
        cap=D(capstr)/100
        nominal=(D('.07')*5*cap*(1-cap)).quantize(D('.000001'),rounding=ROUND_CEILING)
        spent=(5*cap+nominal).quantize(D('.0001'),rounding=ROUND_CEILING)
        total=gain=loss=fee_total=D(0);nw=nl=0
        assert len(xs)==111 and {x['ticker'] for x in xs}==set(rows)
        for x in xs:
            r=rows[x['ticker']]
            assert D(str(x['hold_net']))==D(orders[x['ticker']]['pnl'])
            assert x['won']==r['won']
            cheap=[b for b in buys[x['ticker']] if b[2]<=cap]
            signals=[b for b in cheap if qualify(r,b[0]+feed,feed)]
            times=[b[0] for b in signals]
            found=None
            for t,ident,price,q in cheap:
                if q<5 or not 0<r['close']-t<=60 or not qualify(r,t-order,feed):
                    continue
                j=bisect.bisect_left(times,t-feed-order-5)
                if j<len(times) and times[j]<=t-feed-order:
                    found=(signals[j],(t,ident,price,q))
                    break
            assert bool(found)==x['added'],(scenario,capstr,x['ticker'],'first fill mismatch')
            assert bool(signals)==bool(x['signals'])
            checks+=1
            if found:
                s,f=found
                assert s[1]==x['signal_trade'] and f[1]==x['execution_trade']
                assert s[1]!=f[1] and s[0]+feed+order<=f[0]<=s[0]+feed+order+5
                assert abs(zvalue(r,s[0]+feed,feed)-x['signal_z'])<1e-9
                assert abs(zvalue(r,f[0]-order,feed)-x['recheck_z'])<1e-9
                assert f[2]==D(str(x['execution_print_price']))
                assert f[3]==D(str(x['execution_print_qty']))
                extra=5*int(r['won'])-spent
                assert abs(x['extra_net']-float(extra))<1e-9
                assert abs(x['cash_spent']-float(spent))<1e-9
                assert abs(x['nominal_fee']-float(nominal))<1e-9
                assert abs(x['fee']-float(spent-5*cap))<1e-9
                assert x['quantity']==5
                total+=extra;fee_total+=spent-5*cap
                if r['won']:gain+=extra;nw+=1
                else:loss-=extra;nl+=1
                cashchecks+=1;witnesses+=1
            else:
                assert x['extra_net']==x['cash_spent']==x['fee']==0
        m=results[scenario][capstr]
        assert (nw,nl)==(m['add_wins'],m['add_losses'])
        for key,value in [('extra_net',total),('extra_winner_profit',gain),('extra_losses',loss),
                          ('fees',fee_total),('total_net',D('15.305')+total),
                          ('total_net_friction1c',D('15.305')+total-D('.05')*(nw+nl)),
                          ('total_net_friction2c',D('15.305')+total-D('.10')*(nw+nl))]:
            assert abs(m[key]-float(value))<1e-8,(scenario,capstr,key)

price_rows=json.load(gzip.open(P/'witness_price_ledgers.json.gz'))
price_results=json.loads((P/'witness_price_sensitivity.json').read_text())
price_checks=0
for scenario,caps in price_rows.items():
    for cap,xs in caps.items():
        main={r['ticker']:r for r in ledgers[scenario][cap] if r['added']}
        assert {r['ticker'] for r in xs}==set(main)
        total=D(0)
        for x in xs:
            source=byid[main[x['ticker']]['execution_trade']]
            side=orders[x['ticker']]['side']
            price=D(source[side+'_price_dollars'])
            fee=(D('.07')*5*price*(1-price)).quantize(D('.000001'),rounding=ROUND_CEILING)
            spent=(5*price+fee).quantize(D('.0001'),rounding=ROUND_CEILING)
            net=5*int(rows[x['ticker']]['won'])-spent
            assert D(str(x['price']))==price
            assert abs(x['extra_net']-float(net))<1e-9
            assert abs(x['fee']-float(spent-5*price))<1e-9
            total+=net;price_checks+=1
        assert abs(price_results[scenario][cap]['extra_net']-float(total))<1e-8
for filename,expected in manifest['input_hashes'].items():
    assert hashlib.sha256(Path(filename).read_bytes()).hexdigest()==expected
before=json.loads((P/'preservation_before.json').read_text())
after=json.loads((P/'preservation_after.json').read_text())
assert before==after
out=dict(status='PASS',cohort=111,wins=106,losses=5,price_caps=35,delay_scenarios=4,
         scalar_first_add_rows_verified=checks,decimal_cash_checks=cashchecks,
         independent_source_time_and_witness_checks=witnesses,
         independent_witness_price_cash_checks=price_checks,
         raw_receipt_pages_verified=len(capture['receipts']),raw_trades_verified=len(raw),
         pagination_chains_complete=len(groups),old50c_result_reproduced=True,
         production_files_unchanged=len(before['files']),production_flags_and_five_service_processes_unchanged=True)
(P/'independent_audit.json').write_text(json.dumps(out,indent=2)+'\n')
print(json.dumps(out,indent=2))
