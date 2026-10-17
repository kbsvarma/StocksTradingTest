"""Causal full-input multi-period screen. No credentials or order transport."""
import argparse,bisect,collections,datetime as dt,gzip,hashlib,json,math,pickle,re
from pathlib import Path
import numpy as np
from strategy import RULES,definitions,qualifies,model_features,book_fill,tick_cap,fee

P=Path(__file__).resolve().parent;ROOT=P.parent
ASSETS=('BTC','ETH','SOL','XRP')
FOLDERS=[ROOT/'kalshi_restart_audit_2026_09_12/fresh_data',P/'fresh_data',ROOT/'kalshi_execution_audit_2026_10_02/fresh_data']
def ts(s):
    s=re.sub(r'\.(\d+)',lambda m:'.'+m.group(1)[:6].ljust(6,'0'),s)
    return int(dt.datetime.fromisoformat(s.replace('Z','+00:00')).timestamp())
def day(t):return dt.datetime.fromtimestamp(t,dt.timezone.utc).date().isoformat()
def cohort(t):return 'fit' if t<ts('2026-08-28T00:00:00Z') else 'selection' if t<ts('2026-09-12T00:00:00Z') else 'later'
def quote(c,field='c'):
    try:b,a=float(c['b'+field]),float(c['a'+field])
    except (KeyError,TypeError,ValueError):return None
    return (b,a) if 0<=b<=a<=1 else None
def load_data():
    markets={};bars={a:{} for a in ASSETS};quality=collections.Counter();hashes={}
    for folder in FOLDERS:
        for a in ASSETS:
            for stem in ('markets','candles','spot'):
                p=folder/f'{stem}_{a}.jsonl'
                if not p.exists():continue
                hashes[str(p.relative_to(ROOT))]=hashlib.sha256(p.read_bytes()).hexdigest()
                for line in p.open():
                    r=json.loads(line)
                    if stem=='markets':
                        if r.get('result') not in ('yes','no'):continue
                        if r['ticker'] in markets:
                            assert markets[r['ticker']]['result']==(1 if r['result']=='yes' else -1)
                            continue
                        markets[r['ticker']]={'ticker':r['ticker'],'asset':a,'start':ts(r['open_time']),'close':ts(r['close_time']),
                            'settled':ts(r.get('settlement_ts') or r.get('settlement_time') or r['close_time']),
                            'result':1 if r['result']=='yes' else -1,'ranges':r['price_ranges'],'candles':{}}
                    elif stem=='candles':
                        if r['ticker'] in markets:markets[r['ticker']]['candles'].update({c['t']:c for c in r['candles']})
                    else:
                        for row in r['rows']:bars[a][int(row[0])]=float(row[4])
    stats={}
    for a,bb in bars.items():
        keys=np.arange(min(bb),max(bb)+60,60,dtype=np.int64)
        v=np.array([bb.get(int(t),np.nan) for t in keys]);ret=np.diff(np.log(v))
        # Windows are exactly 60 completed returns, with all 61 prices valid.
        windows=np.lib.stride_tricks.sliding_window_view(ret,60)
        valid=np.isfinite(windows).all(axis=1);sig=np.std(windows,axis=1)
        stats[a]={int(keys[i+60]+60):(float(v[i+60]),float(sig[i])) for i in np.flatnonzero(valid & (sig>0))}
    rows=[]
    for m in sorted(markets.values(),key=lambda m:(m['close'],m['asset'])):
        fs=[];quality['markets_'+m['asset']]+=1
        for T in range(m['start']+120,m['close'],60):
            rem=(m['close']-T)//60
            if not 1<=rem<=13:continue
            quality['minutes']+=1;q=quote(m['candles'].get(T))
            if q is None:quality['missing_quote']+=1;continue
            st=stats[m['asset']].get(T);ref=bars[m['asset']].get(m['start']-60)
            if st is None or ref is None:quality['incomplete_spot']+=1;continue
            side=1 if sum(q)>1 else -1;b,a=q if side==1 else (1-q[1],1-q[0])
            z=side*(st[0]/ref-1)/(st[1]*math.sqrt(rem))
            f=dict(t=T,rem=rem,side=side,bid=b,ask=a,z=z,spot=st[0],ref=ref,sigma=st[1])
            fs.append(f)
        m['features']=fs;rows.append(m)
    (P/'coverage.json').write_text(json.dumps({'quality':dict(quality),'hashes':hashes,
      'markets_by_day':dict(collections.Counter(day(m['close']) for m in rows))},indent=2))
    return rows

def fit(markets):
    xx=[];yy=[];weights=[]
    for m in markets:
        if cohort(m['close'])!='fit':continue
        valid=[f for f in m['features'] if 3<=f['rem']<=10 and .55<=f['ask']<.98 and f['ask']-f['bid']<=.02000001]
        for f in valid:
            xx.append(model_features(f));yy.append(int(f['side']==m['result']));weights.append(1/len(valid))
    X=np.array(xx);y=np.array(yy);w=np.array(weights);coef=np.array([0.,1.,0.,0.]);prior=coef.copy()
    penalty=np.diag([1.,10.,10.,10.])
    for _ in range(50):
        pred=1/(1+np.exp(-np.clip(np.einsum('ij,j->i',X,coef),-30,30)))
        grad=np.einsum('ij,i->j',X,w*(pred-y))+np.einsum('ij,j->i',penalty,coef-prior)
        hessian=np.einsum('ij,ik,i->jk',X,X,w*pred*(1-pred))+penalty
        step=np.linalg.solve(hessian,grad);coef-=step
        if max(abs(step))<1e-10:break
    out={'coefficients':coef.tolist(),'rows':len(y),'market_equivalent_weight':float(w.sum()),
         'fit_end_exclusive':'2026-08-28T00:00:00Z','features':['intercept','logit quote midpoint','clipped z','log(remaining/7)'],'ridge':penalty.diagonal().tolist()}
    (P/'model.json').write_text(json.dumps(out,indent=2));return coef

def load_books():
    books=collections.defaultdict(list)
    with gzip.open(FOLDERS[0]/'books.jsonl.gz','rt') as inp:
        for line in inp:
            r=json.loads(line)
            conv=lambda name:tuple((float(p),float(n)) for p,n in r.get(name,[]) if float(n)>0)
            books[r['ticker']].append((float(r['ts']),conv('yes'),conv('no')))
    for bb in books.values():bb.sort()
    return books

def replay(markets,model,books=None,quantity=1,delay=0,all_signals=False,rules=None):
    out=collections.defaultdict(list);counts=collections.Counter()
    for m in markets:
        bb=books.get(m['ticker'],[]) if books is not None else None
        if bb is not None and not bb:continue
        bts=[b[0] for b in bb] if bb is not None else None
        for rule in (rules or RULES):
            for f in m['features']:
                if not qualifies(rule,f,model):continue
                counts[rule.name+'_signals']+=1
                cap=tick_cap(rule,f,m['ranges'])
                if cap is None:continue
                if bb is not None:
                    j=bisect.bisect_left(bts,f['t']+max(.3,delay))
                    if j==len(bb) or bts[j]>f['t']+35:continue
                    # One observed subsequent book per signal; later-minute signals may retry.
                    fill=book_fill(bb[j],f['side'],cap,rule.lo,quantity)
                    if fill is None:continue
                    entry,paid=fill;entered=bts[j]
                else:
                    q=quote(m['candles'].get(f['t']+60+delay),'o')
                    if q is None:continue
                    entry=q[1] if f['side']==1 else 1-q[0]
                    if not rule.lo-1e-8<=entry<=cap+1e-8:continue
                    paid=fee(entry,quantity);entered=f['t']+delay+3
                won=int(f['side']==m['result'])
                out[rule.name].append(dict(ticker=m['ticker'],asset=m['asset'],t=f['t'],entered=entered,
                    close=m['close'],settled=max(m['close']+30,m['settled']),day=day(m['close']),
                    cohort=cohort(m['close']),entry=entry,cap=cap,fee=paid,quantity=quantity,won=won,
                    pnl=quantity*(won-entry)-paid,pnl_cap=quantity*(won-cap)-fee(cap,quantity),
                    z=f['z'],side=f['side'],rem=f['rem'],ask=f['ask']))
                if not all_signals:break
    return dict(out),dict(counts)

def metrics(rows,stress=.01):
    n=len(rows)
    if not n:return dict(n=0,net=0)
    pnl=[r['pnl']-stress*r['quantity'] for r in rows];daily=collections.defaultdict(float);interval=collections.defaultdict(float)
    for r,p in zip(rows,pnl):daily[r['day']]+=p;interval[r['close']]+=p
    eq=np.r_[0,np.cumsum([interval[t] for t in sorted(interval)])]
    nqty=sum(r['quantity'] for r in rows)
    return dict(n=n,wins=sum(r['won'] for r in rows),losses=n-sum(r['won'] for r in rows),net=round(sum(pnl),5),
       ev_c=round(100*sum(pnl)/nqty,4),drawdown=round(float(max(np.maximum.accumulate(eq)-eq)),5),
       worst_day=round(min(daily.values()),5),days=len(daily),cap_net=round(sum(r['pnl_cap']-stress*r['quantity'] for r in rows),5))

def main():
    p=argparse.ArgumentParser();p.add_argument('--freeze',action='store_true');p.add_argument('--rebuild',action='store_true');a=p.parse_args()
    if a.freeze:(P/'rules.json').write_text(json.dumps(definitions(),indent=2));print('Frozen',len(RULES),'candidates');return
    assert definitions()==json.loads((P/'rules.json').read_text())
    cache=P/'features.pickle'
    if a.rebuild or not cache.exists():
        markets=load_data();cache.write_bytes(pickle.dumps(markets,protocol=5))
    else:markets=pickle.loads(cache.read_bytes())
    print('markets',len(markets),flush=True);model=fit(markets)
    books=load_books();print('book markets',len(books),flush=True)
    summaries={}
    for name,bb,delay in [('quote',None,0),('book',books,0),('quote_delay60',None,60),('book_delay10',books,10)]:
        rows,counts=replay(markets,model,bb,delay=delay)
        (P/(name+'_trades.json')).write_text(json.dumps(rows))
        result={r.name:{c:metrics([x for x in rows.get(r.name,[]) if x['cohort']==c]) for c in ('fit','selection','later')} for r in RULES}
        summaries[name]=result
        print(name,flush=True)
        for rn,rr in result.items():print(rn, ' | '.join(f'{k}: {v["n"]} / ${v["net"]:.3f}' for k,v in rr.items()),flush=True)
    (P/'screen.json').write_text(json.dumps(summaries,indent=2))

if __name__=='__main__':main()
