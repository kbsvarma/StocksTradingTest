"""Independent cash, buy-side, source-time and chronological first-add checks."""
import datetime as dt
import gzip,json,pickle,math,hashlib,bisect
from decimal import Decimal as D,ROUND_CEILING
from pathlib import Path
P=Path(__file__).resolve().parent;B=P.parent/'kalshi_wti_sell_floors_2026_10_15'
data=pickle.load(gzip.open(B/'inputs.pkl.gz','rb'));rows={r['ticker']:r for r in data['actual']}
source=pickle.load(gzip.open(B/'seconds.pkl.gz','rb'))['WTI']
orders={o['ticker']:o for o in json.loads((B/'frozen_orders.json').read_text())['orders']}
trades=json.load(gzip.open(P/'trades.json.gz'));byid={t['trade_id']:t for ts in trades.values() for t in ts}
ledgers=json.load(gzip.open(P/'grid_ledgers.json.gz'));summaries=json.loads((P/'grid_results.json').read_text());configs={c['id']:c for c in json.loads((P/'configs.json').read_text())}
def timestamp(t):
    v=t['created_time'].replace('Z','+00:00')
    return (dt.datetime.strptime(v,'%Y-%m-%dT%H:%M:%S.%f%z') if '.' in v else dt.datetime.fromisoformat(v)).timestamp()
def zvalue(r,at):
    start,values=source;spot=values[math.floor(at-1)-start];rem=r['close']-at
    assert rem>0
    return r['side']*((spot-r['reference'])/r['reference'])/(r['entry_sigma']*math.sqrt(rem/60))
def qualify(r,at,c):
    _,lo,hi=c['window'];rem=r['close']-at
    if not lo<rem<=hi:return False
    z=zvalue(r,at);_,low,high=c['zfilter']
    return (low is None or z>=low) and (high is None or z<high)
cash_checks=0;witness_checks=0
for key,xs in ledgers.items():
    c=configs[key];assert len(xs)==111 and {x['ticker'] for x in xs}==set(rows)
    total=D(0);gain=D(0);loss=D(0)
    for x in xs:
        assert D(str(x['hold_net']))==D(orders[x['ticker']]['pnl'])
        extra=D(0)
        if x['added']:
            p=D(c['cap'])/100;fee=(D('.07')*5*p*(1-p)).quantize(D('.000001'),rounding=ROUND_CEILING)
            spent=(5*p+fee).quantize(D('.0001'),rounding=ROUND_CEILING);extra=5*int(x['won'])-spent
            assert abs(float(spent)-x['cash_spent'])<1e-9
            assert abs(float(fee)-x['fee'])<1e-9
            assert abs(float(extra)-x['extra_net'])<1e-9
            cash_checks+=1
            r=rows[x['ticker']];side=orders[x['ticker']]['side'];a=byid[x['signal_trade']];b=byid[x['execution_trade']]
            assert a['taker_side']==b['taker_side']==side
            assert a['ticker']==b['ticker']==r['ticker'] and a['trade_id']!=b['trade_id']
            assert not a['is_block_trade'] and not b['is_block_trade']
            ta,tb=timestamp(a),timestamp(b)
            assert ta>r['entered'] and tb<r['close']
            assert D(a[side+'_price_dollars'])<=p and D(b[side+'_price_dollars'])<=p and D(b['count_fp'])>=5
            assert ta+2<=tb<=ta+7
            assert abs(ta+1-x['signal_time'])<1e-6 and abs(tb-x['fill_time'])<1e-6
            assert qualify(r,ta+1,c) and qualify(r,tb-1,c)
            assert c['window'][1]<r['close']-tb<=c['window'][2]
            assert abs(zvalue(r,ta+1)-x['signal_z'])<1e-9 and abs(zvalue(r,tb-1)-x['fill_z'])<1e-9
            witness_checks+=1
        else:assert x['extra_net']==0
        total+=extra
        if x['won']:gain+=extra
        else:loss-=extra
    m=summaries[key]['full'];assert total==gain-loss
    assert abs(float(total)-m['extra_net'])<1e-8
    assert abs(float(D('15.305')+total)-m['total_net'])<1e-8
# Independently replay every displayed configuration by chronological execution
# candidates, rather than the implementation's signal-first search.
display=json.loads((P/'selected_ledgers.json').read_text());first_checks=0
for key,xs in display.items():
    c=configs[key];cap=D(c['cap'])/100
    for x in xs:
        r=rows[x['ticker']];side=orders[x['ticker']]['side'];buys=[]
        for tr in trades.get(r['ticker'],[]):
            t=timestamp(tr)
            if r['entered']<t<r['close'] and tr['taker_side']==side and D(tr[side+'_price_dollars'])<=cap:
                buys.append((t,tr['trade_id'],D(tr['count_fp'])))
        buys.sort();signals=[(t,ident) for t,ident,q in buys if qualify(r,t+1,c)]
        sts=[t for t,ident in signals];found=None
        for t,ident,q in buys:
            if q<5 or not qualify(r,t-1,c) or not c['window'][1]<r['close']-t<=c['window'][2]:continue
            j=bisect.bisect_left(sts,t-7)
            if j<len(sts) and sts[j]<=t-2:
                found=(signals[j][1],ident);break
        assert bool(found)==x['added'],(key,r['ticker'],'addition mismatch')
        if found:assert found==(x['signal_trade'],x['execution_trade']),(key,r['ticker'],found)
        first_checks+=1
capture=json.loads((P/'capture_audit.json').read_text());raw_seen={}
for receipt in capture['receipts']:
    p=P/'public'/receipt['file'];assert hashlib.sha256(p.read_bytes()).hexdigest()==receipt['hash']
    payload=json.load(gzip.open(p))
    for t in payload['response']['trades']:
        if t['trade_id'] in raw_seen:assert raw_seen[t['trade_id']]==t
        raw_seen[t['trade_id']]=t
assert raw_seen==byid and len(raw_seen)==capture['trades']
touch=json.loads((P/'price_touch_ledgers.json').read_text());touch_checks=0
for capstr,xs in touch.items():
    cap=D(capstr)/100
    for x in xs:
        r=rows[x['ticker']];eligible=[]
        for end,c in data['markets'][r['ticker']]['candles'].items():
            if not r['entered']<end<=r['close']:continue
            suffix='l' if end-60>=r['entered'] else 'c'
            v=D(str(c['a'+suffix])) if r['side']==1 else 1-D(str(c['b'+('h' if suffix=='l' else 'c')]))
            if 0<v<=cap:eligible.append(end)
        assert bool(eligible)==x['added'];touch_checks+=1
out=dict(grid_variants=len(ledgers),cohort=111,grid_rows_checked=len(ledgers)*111,decimal_cash_checks=cash_checks,
    independent_source_chronology_witness_checks=witness_checks,independent_first_add_checks=first_checks,
    ask_touch_checks=touch_checks,public_receipt_pages_verified=len(capture['receipts']),deduplicated_trades_verified=len(raw_seen),no_live_imports_or_orders=True)
before=json.loads((P/'preservation_before.json').read_text());after=json.loads((P/'preservation_after.json').read_text())
assert before==after
out.update(live_code_flags_service_pids_unchanged=True,production_files_verified=len(before['files']))
(P/'independent_audit.json').write_text(json.dumps(out,indent=2)+'\n');print(json.dumps(out,indent=2))
