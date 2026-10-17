"""Independent chronological depth entry and ride-to-settlement replay."""
import bisect,collections,gzip,json,sys
import numpy as np
import study as s
P=s.P;OLD=s.ROOT/'kalshi_protected_profit_revisit_2026_10_03'
def top(levels):
    levels=sorted([(float(p),float(n)) for p,n in levels if float(n)>0],reverse=True);r=[levels[0][0] if levels else None]
    for N in (1,5):
        cost=0.;qty=0.;worst=None
        for p,n in levels:
            take=min(n,N-qty);qty+=take;cost+=p*take;worst=p
            if qty>=N-1e-8:break
        r.extend([cost/N if qty>=N-1e-8 else None,worst if qty>=N-1e-8 else None])
    return r
def books(recent):
    out=collections.defaultdict(list)
    if recent:
        base=P/'inputs/stream_top' if (P/'inputs/stream_top').exists() else OLD/'stream_top'
        for f in sorted(base.glob('top*.gz')):
            with gzip.open(f,'rt') as inp:r=json.load(inp)
            for ticker,bb in r['rows'].items():
                assert ticker not in out;out[ticker]=[[b[0]/1000,*b[1:]] for b in bb]
    else:
        f=P/'inputs/books.jsonl.gz' if (P/'inputs/books.jsonl.gz').exists() else OLD/'input_sources/books.jsonl.gz'
        with gzip.open(f,'rt') as inp:
            for line in inp:
                r=json.loads(line)
                if r['ticker'].startswith(('KXBTC15M','KXETH15M')):out[r['ticker']].append([r['ts'],*top(r['yes']),*top(r['no'])])
    for bb in out.values():bb.sort(key=lambda b:b[0])
    return out

def fill(bb,T,fav,di,latency=.25,n=1):
    if not bb or T<bb[0][0]:return None
    times=[b[0] for b in bb];k=bisect.bisect_left(times,T+3+latency);direction=fav*di
    off=5 if direction==1 else 0;foff=5 if fav==1 else 0;own=0 if fav==1 else 5;depth=2 if n==1 else 4
    while k<len(bb) and times[k]<=T+25:
        b=bb[k];best=b[1+foff];other=b[1+own];v=b[depth+off];worst=b[depth+off+1]
        if all(q is not None for q in (best,other,v,worst)):
            favorite_ask=1-best;spread=favorite_ask-other;entry=1-v;limit=1-worst
            if -1e-8<=spread<=.02000001 and .55-1e-8<=favorite_ask<=.65+1e-8 and limit<=(.65 if di==1 else .47)+1e-8:return k,entry
        k+=1
    return None

def replay(c,d,allbooks,latency=.25,n=1):
    signal_data=dict(d,valid=d['signal_valid']);v=s.mask(c,signal_data);rows=[];den=collections.Counter()
    for mi in np.flatnonzero(d['asset']==c['asset']):
        m=d['mm'][mi];bb=allbooks.get(m['ticker'])
        if not bb or bb[-1][0]<m['close']-35:continue
        den[s.PERIODS[d['co'][mi]]]+=1;found=None
        for j in np.flatnonzero(v[mi]):
            T=m['start']+60*(int(j)+1);fav=int(d['side'][mi,j]);f=fill(bb,T,fav,c['direction'],latency,n)
            if f is not None:found=j,T,fav,f;break
        if found is None:continue
        j,T,fav,(k,entry)=found;direction=fav*c['direction'];won=int(direction==m['result']);fwon=int(fav==m['result']);cross=False;dip=False;nobs=0;favorite_ask_at_entry=1-(bb[k][6] if fav==1 else bb[k][1])
        for b in bb[k+1:]:
            if b[0]>=m['close']:break
            y,no=b[1],b[6]
            if y is None or no is None or y+no>1+1e-8:continue
            mid=(y+1-no)/2;bid=y if fav==1 else no;nobs+=1
            cross|=(mid-.5)*fav< -1e-8;dip|=bid<=favorite_ask_at_entry-.10+1e-8
        debit=float(s.cash(entry,n));rows.append(dict(ticker=m['ticker'],asset=m['asset'],cohort=s.PERIODS[d['co'][mi]],close=m['close'],settled=max(m['close']+30,m['settled']),decision=T,entered=bb[k][0],rem=14-int(j),side=direction,favside=fav,quantity=n,entry=entry,debit=debit,won=won,favorite_won=fwon,z=float(d['z'][mi,j]) if np.isfinite(d['z'][mi,j]) else None,momentum=float(d['mom'][mi,j]) if np.isfinite(d['mom'][mi,j]) else None,pnl=n*won-debit,subsequent_observations=nobs,crossed_below_half=cross if nobs else None,dipped_ten_cents=dip if nobs else None))
    return rows,den
def main(recent):
    x,mm=s.load(recent);d=s.features(x,mm);bb=books(recent);f=json.loads((P/'frozen_selection.json').read_text());cc={r['config']['id']:r['config'] for r in f['selected']};cc.update({c['id']:c for c in s.configs() if c['filter']=='none' and c['remlo']==2 and c['remhi']==14});out={};name='recent_books' if recent else 'historical_books'
    for cid,c in cc.items():
        for latency in (.25,3,10):
            for n in (1,5):
                rr,den=replay(c,d,bb,latency,n);key=f'{cid}_d{latency}_n{n}';s.save(f'{name}_trades_{key}.json',rr)
                # Normalize only for descriptive rates; original full-size ledgers retained.
                normalized=[dict(r,debit=r['debit']/n,pnl=r['pnl']/n) for r in rr]
                periods={}
                for co in den:
                    base=s.metrics([r for r in normalized if r['cohort']==co],den[co]);one=s.metrics([r for r in normalized if r['cohort']==co],den[co],.01)
                    for item in (base,one):
                        item['net']*=n;item['drawdown']*=n;item['daily']={k:v*n for k,v in item['daily'].items()};item['quantity']=n
                    periods[co]=dict(base=base,cost1c=one)
                out[key]=dict(config=c,latency=latency,quantity=n,periods=periods)
                if n==1 and latency==.25:print(name,cid,[(co,p['base']['trades'],p['base']['net']) for co,p in periods.items()],flush=True)
    s.save(name+'_results.json',out)
if __name__=='__main__':main(sys.argv[1]=='recent')
