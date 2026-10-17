from pathlib import Path
from decimal import Decimal as D
import collections,gzip,json,pickle
P=Path(__file__).resolve().parent
SRC=Path('/home/varma/kalshi_btc_eth_quant_2026_10_02/forward_state/data')
def main():
    ledger=json.loads((P/'ledger_stable_tp2_t60_L250_cash100.json').read_text());mm=json.loads((P/'capture_manifest.json').read_text());worst=sorted((r for r in ledger if r['quantity']),key=lambda r:r['net'])[:3];out=[]
    for o in worst:
        close=(o['signal_ts']//900000+1)*900;info=next(x for x in mm if x['accepted'] and int(x['file'].split('_')[1])==close)
        with gzip.open(P/'compact'/info['target'],'rb') as f:d=pickle.load(f)
        a=0 if o['asset']=='BTC' else 1;rr=[r for r in d['rows'] if r[1]==0 and r[2]==a and o['signal_ts']-5000<=r[0]<=o['ended']+2000];selected=[];last=0
        for r in rr:
            if r[0]-last>=500 or r[0] in {o['signal_ts'],o['accepted_ts'],o['trigger_ts'],o['ended']}:
                selected.append(r);last=r[0]
        want={r[0]:r for r in selected};books={};prev=None;checked=[];trades=[];benchmark=[];ids={x['trade_id'] for x in o['legs'] if x['trade_id']}
        with gzip.open(SRC/info['file'],'rt') as inp:
            for line in inp:
                if not line.strip():continue
                r=json.loads(line);now=r['client_received_at_ms'];m=r.get('msg',{});k=r['type']
                if now>o['ended']+2100:break
                if now!=prev and now in want:
                    b=books[o['ticker']];sample=want[now]
                    for side,actual in [('yes',sample[3]),('no',sample[4])]:
                        expected=[[int(p*10000),int(b[side][p]*100)] for p in sorted(b[side],reverse=True)[:10]];assert actual==expected
                    own,opp=(sample[3],sample[4]) if o['side']==1 else (sample[4],sample[3]);checked.append(dict(ts=now,bid=own[0][0]/10000,ask=1-opp[0][0]/10000,bid_qty=own[0][1]/100,fair_change=sample[7],fair_range=sample[8]))
                prev=now
                if k=='orderbook_snapshot':books[m['market_ticker']]={s:{D(p):D(q) for p,q in m.get(s+'_dollars_fp',[]) if D(q)>0} for s in ('yes','no')}
                elif k=='orderbook_delta':
                    b=books[m['market_ticker']][m['side']];p=D(m['price_dollars']);q=b.get(p,D(0))+D(m['delta_fp'])
                    if q>0:b[p]=q
                    else:b.pop(p,None)
                elif k=='trade' and m['trade_id'] in ids:trades.append(dict(received=now,**m))
                elif k in ('cfbenchmarks_value','cfbenchmarks_value_5hz') and now>=o['signal_ts']-5000 and m['index_id']==('BRTI' if a==0 else 'ETHUSD_RTI'):
                    v=json.loads(m['data']);benchmark.append(dict(received=now,type=k,value=v['value'],source=v['time']))
        assert len(checked)==len(want) and len(trades)==len(ids)
        out.append(dict(order=o,source=info['file'],verified_books=checked,verified_fill_prints=trades,benchmark=benchmark))
    (P/'worst_trade_audit.json').write_text(json.dumps(out,indent=2));print(json.dumps([dict(ticker=x['order']['ticker'],net=x['order']['net'],checked_books=len(x['verified_books']),entry=x['order']['price']/10000,exit=x['order']['legs'][-1]['price']/10000) for x in out]))
if __name__=='__main__':main()
