"""Frozen inputs plus public historical trades only; no account access."""
from pathlib import Path
from decimal import Decimal as D
import datetime as dt
import gzip,json,pickle,time,hashlib
import requests
P=Path(__file__).resolve().parent;BASE=P.parent/'kalshi_wti_sell_floors_2026_10_15'
def save(n,x):(P/n).write_text(json.dumps(x,indent=2,allow_nan=False)+'\n')
def ask(c,side,suffix):
    raw=c['a'+suffix] if side==1 else c['b'+{'l':'h','h':'l','o':'o','c':'c'}[suffix]]
    if raw is None:return None
    return D(str(raw)) if side==1 else 1-D(str(raw))
def main():
    data=pickle.load(gzip.open(BASE/'inputs.pkl.gz','rb'));rows=data['actual']
    assert len(rows)==111 and sum(r['won'] for r in rows)==106
    (P/'public').mkdir(exist_ok=True)
    candidates=[]
    for r in rows:
        cs=data['markets'][r['ticker']]['candles']
        quotes=[ask(c,r['side'],'l') for e,c in cs.items() if r['entered']<e<=r['close']]
        assert quotes and all(q is not None for q in quotes)
        if min(quotes)<=D('.50'):candidates.append(r)
    save('candidate_markets.json',[{k:r[k] for k in ['ticker','won','side','entered','close','lane']} for r in candidates])
    session=requests.Session();receipts=[];alltrades={};calls=0
    for i,r in enumerate(candidates):
        cursor='';seen_cursors=set();seen={};page=0
        while True:
            params=dict(ticker=r['ticker'],min_ts=int(r['entered']),max_ts=int(r['close']),limit=1000,is_block_trade='false')
            if cursor:params['cursor']=cursor
            dest=P/'public'/f"{r['ticker']}_{page:03d}.json.gz"
            if dest.exists():x=json.load(gzip.open(dest))
            else:
                for attempt in range(4):
                    started=time.time();resp=session.get('https://external-api.kalshi.com/trade-api/v2/markets/trades',params=params,timeout=20);calls+=1
                    if resp.status_code in [429,500,502,503,504]:time.sleep(1+attempt);continue
                    resp.raise_for_status();break
                else:raise RuntimeError('GET failed: '+r['ticker'])
                x=dict(started=started,received=time.time(),url=resp.url,params=params,status=resp.status_code,response=resp.json())
                with gzip.open(dest,'wt') as h:json.dump(x,h)
                time.sleep(.15)
            assert x['params']==params
            for tr in x['response']['trades']:
                assert tr['ticker']==r['ticker'] and not tr.get('is_block_trade',False)
                ident=tr['trade_id']
                if ident in seen:assert seen[ident]==tr
                seen[ident]=tr
            cursor=x['response'].get('cursor','');receipts.append(dict(file=dest.name,hash=hashlib.sha256(dest.read_bytes()).hexdigest(),count=len(x['response']['trades']),cursor_present=bool(cursor)))
            page+=1
            if not cursor:break
            assert cursor not in seen_cursors;seen_cursors.add(cursor)
            assert page<100,'unexpected market size: preserve pending receipt and inspect'
        trades=sorted(seen.values(),key=lambda tr:(tr['created_time'],tr['trade_id']))
        alltrades[r['ticker']]=trades
        print(i+1,'/',len(candidates),r['ticker'],'pages',page,'trades',len(trades),flush=True)
    with gzip.open(P/'trades.json.gz','wt') as h:json.dump(alltrades,h)
    save('capture_audit.json',dict(cohort=111,candidate_markets=len(candidates),pages=len(receipts),trades=sum(map(len,alltrades.values())),new_gets=calls,receipts=receipts,all_cursors_exhausted=True,
      input_hashes={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in [BASE/'inputs.pkl.gz',BASE/'seconds.pkl.gz',BASE/'frozen_orders.json',P/'PLAN.txt']}))
if __name__=='__main__':main()
