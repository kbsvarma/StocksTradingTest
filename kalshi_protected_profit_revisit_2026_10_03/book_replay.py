"""Causal entry signals and observed bid/depth exits. Offline public-data only."""
from pathlib import Path
import bisect,collections,gzip,json,math,pickle,sys
import numpy as np
from state_machine import Protection
import study as s
P=s.P

def entry_signals(c,x,m):
    for j in range(14):
        rem=14-j;b,a=x[j,:2]
        if not c['remlo']<=rem<=c['remhi'] or not np.isfinite(b+a):continue
        side=1 if b+a>1 else -1;bid=b if side==1 else 1-a;ask=a if side==1 else 1-b
        if not c['lo']-1e-8<=ask<=c['hi']+1e-8 or ask-bid>.02000001:continue
        z=x[j,6]*side;mom=x[j,7]*side;p=x[j,8] if side==1 else 1-x[j,8];f=c['filter']
        if f==1 and (not np.isfinite(z) or z<.25):continue
        if f==2 and (not np.isfinite(z+mom) or z<0 or mom<.25):continue
        if f>=3 and (not np.isfinite(p) or p-ask<(.03 if f==3 else .06)):continue
        yield dict(t=m['start']+60*(j+1),side=side,rem=rem)

def top(levels):
    levels=sorted([(float(p),float(n)) for p,n in levels if float(n)>0],reverse=True)
    result=[levels[0][0] if levels else None]
    for N in (1,5):
        qty=0;cost=0;worst=None
        for p,n in levels:
            take=min(n,N-qty);cost+=p*take;qty+=take;worst=p
            if qty>=N-1e-8:break
        result.extend([cost/N if qty>=N-1e-8 else None,worst if qty>=N-1e-8 else None])
    return result

def trade(c,x,m,books,latency=.25,quantity=1):
    if not books:return None
    times=[b[0] for b in books];end=m['close'];found=None;buyoff=2 if quantity==1 else 4
    for f in entry_signals(c,x,m):
        if f['t']<times[0]:continue
        k=bisect.bisect_left(times,f['t']+3+latency)
        while k<len(books) and times[k]<=min(f['t']+25,end-1):
            b=books[k];off=5 if f['side']==1 else 0;ownoff=0 if f['side']==1 else 5
            best=b[1+off];other=b[1+ownoff];v=b[buyoff+off];worst=b[buyoff+off+1]
            if all(vv is not None for vv in (best,other,v,worst)):
                ask=1-best;spread=ask-other;entry=1-v;cap=1-worst
                if -1e-8<=spread<=.02000001 and c['lo']-1e-8<=ask<=c['hi']+1e-8 and cap<=c['hi']+1e-8:
                    found=f,k,entry;break
            k+=1
        if found:break
    if found is None:return None
    f,k,entry=found;entered=times[k];state=Protection.from_config(entry,f['rem'],c);triggered=None
    exit_price=int(f['side']==m['result']);exit_ts=max(m['close']+30,m['settled']);reason='settlement';trigger_bid=None;level=None
    for b in books[k+1:]:
        now=b[0]
        if now>=end:break
        off=0 if f['side']==1 else 5;bid=b[1+off]
        if bid is None or b[1] is None or b[6] is None or b[1]+b[6]>1+1e-8:continue
        if triggered is None:
            event=state.observe(bid)
            if event:
                triggered=now;trigger_bid=bid
                level=state.stop if event=='hard_stop' else (state.safe if event=='safe_reversal' else state.ideal)-state.buffer
        if triggered is not None and now>=triggered+latency:
            price=b[buyoff+off]
            if price is not None:
                exit_price=price;exit_ts=now;reason=state.pending;break
    debit=s.cashchange(entry,quantity,True);credit=s.cashchange(exit_price,quantity,False) if reason!='settlement' else quantity*exit_price
    return dict(ticker=m['ticker'],asset=m['asset'],close=m['close'],cohort=s.PERIODS[s.cohort(m['close'])],decision=f['t'],entered=entered,entry=entry,side=f['side'],exit=exit_price,exit_ts=exit_ts,reason=reason,safe_armed=state.armed>=1,ideal_armed=state.armed==2,quantity=quantity,legs=1+int(reason!='settlement'),debit=debit,credit=credit,pnl=debit+credit,hold_pnl=debit+quantity*int(m['result']==f['side']),triggered=triggered,trigger_bid=trigger_bid,nominal_exit_level=level)

def historical():
    path=P/'input_sources/books.jsonl.gz' if (P/'input_sources/books.jsonl.gz').exists() else s.ROOT/'kalshi_z_time_research_2026_10_03/input_sources/0/books.jsonl.gz';books=collections.defaultdict(list)
    with gzip.open(path,'rt') as inp:
        for line in inp:
            r=json.loads(line)
            if not r['ticker'].startswith(('KXBTC15M','KXETH15M')):continue
            books[r['ticker']].append([r['ts'],*top(r['yes']),*top(r['no'])])
    for bb in books.values():bb.sort(key=lambda b:b[0])
    x=np.load(P/'market_arrays.npy');mm=json.loads((P/'markets.json').read_text());run(x,mm,books,'historical_books',latencies=(.25,1),quantities=(1,5))

def run(x,mm,books,name,latencies=(.25,1),quantities=(1,)):
    frozen=json.loads((P/'frozen_selection.json').read_text());cc={z['config']['id']:z['config'] for z in frozen['selected'] if z['config']};cc.update({c['id']:c for c in frozen['references']});out={}
    for cid,c in cc.items():
        for latency in latencies:
            for n in quantities:
                rr=[];den=collections.Counter();gaps=[]
                for i,m in enumerate(mm):
                    if m['asset']!=c['asset'] or m['ticker'] not in books:continue
                    bb=books[m['ticker']]
                    if not bb or bb[-1][0]<m['close']-35:continue
                    den[s.PERIODS[s.cohort(m['close'])]]+=1;gaps.extend(b[0]-a[0] for a,b in zip(bb,bb[1:]))
                    r=trade(c,x[i],m,bb,latency,n)
                    if r:rr.append(r)
                key=f'{cid}_delay{latency}_n{n}';s.save(f'{name}_trades_{key}.json',rr)
                out[key]=dict(config=c,latency=latency,quantity=n,periods={co:dict(base=s.metrics([r for r in rr if r['cohort']==co],den[co]),stress1c=s.metrics([r for r in rr if r['cohort']==co],den[co],.01)) for co in den},observation_gap_percentiles=np.quantile(gaps,[.5,.9,.99]).tolist() if gaps else [])
                print(name,key,[(co,v['base']['trades'],v['base']['net']) for co,v in out[key]['periods'].items()],flush=True)
    s.save(name+'_results.json',out)

def recent():
    books={}
    for path in sorted((P/'stream_top').glob('top*.gz')):
        with gzip.open(path,'rt') as inp:data=json.load(inp)
        for ticker,bb in data['rows'].items():
            if ticker in books:raise ValueError('Duplicate complete market capture')
            books[ticker]=[[r[0]/1000,*r[1:]] for r in bb]
    x=np.load(P/'oct3_arrays.npy');mm=json.loads((P/'oct3_markets.json').read_text());run(x,mm,books,'recent_stream',latencies=(.25,1,3),quantities=(1,5))

if __name__=='__main__':historical() if sys.argv[1]=='historical' else recent()
