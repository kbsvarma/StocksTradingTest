"""Independent Decimal accounting and raw-book checks, no exchange calls."""
from pathlib import Path
from decimal import Decimal as D,ROUND_CEILING,ROUND_FLOOR
import collections,gzip,json,pickle
P=Path(__file__).resolve().parent
SRC=Path('/home/varma/kalshi_btc_eth_quant_2026_10_02/forward_state/data')
def ledger_audit():
    stats=collections.Counter();maxdelay=0;minhead=10**20
    for path in sorted(P.glob('ledger_*.json')):
        for o in json.loads(path.read_text()):
            g={};b=s=0;seen=set()
            for x in o['legs']:
                stats['legs']+=1;q=D(x['qty'])/100;p=D(x['price'])/10000
                if x['buy']:b+=x['qty']
                else:s+=x['qty'];assert s<=b
                if x['trade_id']:
                    assert x['trade_id'] not in seen;seen.add(x['trade_id']);z=o['entry'] if x['buy'] else o['exit'];assert z['accepted']<=x['source']<z['until'];assert x['t']>=x['source'];minhead=min(minhead,x['source']-z['accepted'])
                    if not x['buy']:assert z['accepted']>=o['first_fill']
                key=x['order'];v=g.setdefault(key,[D(0),D(0),x['buy']]);v[0]+=q*p;v[1]+=D(0) if x['maker'] else D('.07')*q*p*(1-p)
            net=fees=D(0)
            for cost,f,buy in g.values():
                f=f.quantize(D('.000001'),rounding=ROUND_CEILING);fees+=f
                net+=-(cost+f).quantize(D('.0001'),rounding=ROUND_CEILING) if buy else (cost-f).quantize(D('.0001'),rounding=ROUND_FLOOR)
            assert abs(float(net)-o['net'])<1e-8,(path.name,o['id'],net,o['net'])
            assert abs(float(fees)-o['fees'])<1e-8
            assert b==s and b<=500 and abs(b/100-o['quantity'])<1e-9
            stats['orders']+=1;stats['filled']+=b>0;maxdelay=max(maxdelay,o['max_trade_delay'])
    return dict(**stats,max_trade_delay_ms=maxdelay,minimum_fill_after_acceptance_ms=minhead)
def raw_audit():
    mm=json.loads((P/'capture_manifest.json').read_text());valid=[x for x in mm if x['accepted']];out=[]
    for x in [valid[0],valid[len(valid)//2],valid[-1]]:
        with gzip.open(P/'compact'/x['target'],'rb') as f:data=pickle.load(f)
        rows=[r for r in data['rows'] if r[1]==0];sample=collections.defaultdict(list)
        for r in rows[::97]:sample[r[0]].append(r)
        books={};previous=None;checks=0
        with gzip.open(SRC/x['file'],'rt') as f:
            for line in f:
                if not line.strip():continue
                r=json.loads(line);now=r['client_received_at_ms'];m=r.get('msg',{});kind=r['type']
                if now!=previous and now in sample:
                    for rr in sample[now]:
                        ticker=data['meta'][rr[2]]['ticker'];book=books[ticker]
                        for side,actual in [('yes',rr[3]),('no',rr[4])]:
                            expect=[[int(p*10000),int(book[side][p]*100)] for p in sorted(book[side],reverse=True)[:10]];assert expect==actual,(x['file'],now,side);checks+=1
                previous=now
                if kind=='orderbook_snapshot':books[m['market_ticker']]={s:{D(p):D(q) for p,q in m.get(s+'_dollars_fp',[]) if D(q)>0} for s in ('yes','no')}
                elif kind=='orderbook_delta':
                    b=books[m['market_ticker']][m['side']];p=D(m['price_dollars']);v=b.get(p,D(0))+D(m['delta_fp'])
                    if v>0:b[p]=v
                    else:b.pop(p,None)
        assert checks==2*sum(map(len,sample.values()));out.append(dict(file=x['file'],side_book_checks=checks))
    return out
if __name__=='__main__':
    out=dict(accounting=ledger_audit(),raw_books=raw_audit());(P/'audit.json').write_text(json.dumps(out,indent=2));print(json.dumps(out))
