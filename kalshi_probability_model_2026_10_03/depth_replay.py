"""Causal recent-book check using recorded CF receipts and displayed size one."""
import bisect,collections,gzip,json
import numpy as np
import study as s
from model import *
P=s.P
def main():
    z,mm=s.load();spec=json.loads((P/'models.json').read_text());dist=np.load(P/'distributions.npz')
    receipts=json.load(gzip.open(P/'inputs/cf_receipts.json.gz','rt'))
    receipt={(r[0],r[1]):r for r in receipts['rows']};books={};meta={}
    base=P/'inputs/stream_top'
    for path in sorted(base.glob('top*.gz')):
        d=json.load(gzip.open(path,'rt'))
        for m in d['metadata']['markets']:meta[m['ticker']]=m
        for ticker,b in d['rows'].items():books[ticker]=(np.array([r[0]/1000 for r in b]),b)
    bymarket=collections.defaultdict(list)
    for r in z:
        if r[14]==4:bymarket[int(r[0])].append(r)
    audit=collections.Counter();out={k:[] for k in s.KINDS};den=collections.Counter()
    for mi,rows in bymarket.items():
        m=mm[mi];a=m['asset'];ticker=m['ticker']
        if ticker not in books:continue
        if meta[ticker]['floor_strike'] is None:
            audit['missing_ex_ante_strike_markets']+=1;continue
        assert abs(meta[ticker]['floor_strike']-m['strike'])<1e-8
        den[a]+=1;times,bb=books[ticker];entered=set()
        for r in rows:
            _,T,rem,S,sigma,threshold,*_=r;decision=T+3
            rec=receipt.get(({'BTC':'BRTI','ETH':'ETHUSD_RTI'}[a],int(T*1000-1000)))
            if rec is None or rec[2]/1000>decision: audit['missing_or_late_index']+=1;continue
            if abs(rec[3]-S)>1e-8:audit['index_mismatch']+=1;continue
            audit['causal_index_matches']+=1
            ix=bisect.bisect_right(times,decision)-1
            if ix<0 or decision-times[ix]>1:audit['missing_fresh_signal_book']+=1;continue
            b=bb[ix];yb,nb=b[1],b[6]
            if yb is None or nb is None:continue
            spread=1-nb-yb
            if not -1e-8<=spread<=.02000001:continue
            mid=(yb+1-nb)/2;physical=float(empirical_probability(threshold,dist[a+'_x'],dist[a+'_c']))
            ps=dict(market=mid,gaussian=float(gaussian(threshold)),empirical=physical,blended=float(blend(mid,physical,spec['fitted_parameters'][a]['coefficients'])))
            for kind,p in ps.items():
                if kind in entered:continue
                options=[]
                for side,prob,ask in ((1,p,1-nb),(-1,1-p,1-yb)):
                    ok=qualifies(prob,ask,spread) if kind!='market' else (.55-1e-8<=ask<=.65+1e-8)
                    if ok:options.append((prob-float(debit(ask)),side,prob))
                if not options:continue
                _,side,prob=max(options)
                # First observed book at/after the 250ms order arrival; do not search for a favorable future price.
                ex=bisect.bisect_left(times,decision+.25)
                if ex==len(times) or times[ex]>decision+1:continue
                f=bb[ex];offset=5 if side==1 else 0;vwap=f[2+offset];worst=f[3+offset]
                if any(v is None for v in (vwap,worst,f[1],f[6])):continue
                price=1-vwap;limit=1-worst;fs=1-f[1]-f[6]
                if not .55-1e-8<=price<=limit<=.65+1e-8 or not -1e-8<=fs<=.02000001:continue
                if kind!='market' and not qualifies(prob,limit,max(0,fs)):continue
                won=m['result'] if side==1 else 1-m['result'];cash=float(debit(price));entered.add(kind)
                out[kind].append(dict(ticker=ticker,asset=a,cohort='october3',decision=decision,entered=times[ex],close=m['close'],settled=m['settled'],rem=int(rem),side=side,entry=price,debit=cash,probability=prob,won=won,pnl=won-cash-.0025,model=kind,index_source_ms=rec[1],index_received_ms=rec[2]))
    summary={}
    for k,rows in out.items():
        s.save('depth_trades_'+k+'.json',rows);summary[k]={a:dict(base=s.metrics([r for r in rows if r['asset']==a],den[a],4),cost1c=s.metrics([r for r in rows if r['asset']==a],den[a],4,.01)) for a in s.ASSETS}
    s.save('depth_results.json',dict(audit=dict(audit),windows=dict(den),models=summary));print(json.dumps(dict(audit=dict(audit),results={k:{a:(v['base']['trades'],v['base']['net']) for a,v in result.items()} for k,result in summary.items()})),flush=True)
if __name__=='__main__':main()
