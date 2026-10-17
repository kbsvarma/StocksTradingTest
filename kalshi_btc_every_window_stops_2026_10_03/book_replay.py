"""Displayed-size-one, chronological book replay with stop observation/arrival delay."""
import collections,gzip,json,math,pickle,sys
import numpy as np
from numba import njit
import study as s
from engine import cash,direction,breach
P=s.P
def top(levels):
    levels=sorted([(float(p),float(q)) for p,q in levels if float(q)>0],reverse=True)
    if not levels:return [np.nan]*3
    remaining=1.;cost=0.;worst=np.nan
    for p,q in levels:
        take=min(q,remaining);cost+=p*take;remaining-=take;worst=p
        if remaining<=1e-9:break
    return [levels[0][0],cost if remaining<=1e-9 else np.nan,worst if remaining<=1e-9 else np.nan]
def prepare():
    books=collections.defaultdict(list);sources=[]
    raw=s.R/'kalshi_z_time_research_2026_10_03/input_sources/0/books.jsonl.gz';sources.append(dict(path=str(raw),sha256=s.sha(raw)))
    with gzip.open(raw,'rt') as f:
        for line in f:
            r=json.loads(line)
            if r['ticker'].startswith('KXBTC15M'):books[r['ticker']].append([r['ts'],*top(r['yes']),*top(r['no'])])
    for path in sorted((s.R/'kalshi_probability_model_2026_10_03/inputs/stream_top').glob('top*.gz')):
        r=json.load(gzip.open(path,'rt'));sources.append(dict(path=str(path),sha256=s.sha(path)))
        for ticker,rows in r['rows'].items():
            if not ticker.startswith('KXBTC15M'):continue
            if ticker in books:raise ValueError('Overlapping archived book source')
            books[ticker]=[[row[0]/1000,*[np.nan if row[j] is None else row[j] for j in (1,2,3,6,7,8)]] for row in rows]
    data={k:np.array(sorted(v,key=lambda r:r[0]),dtype=float) for k,v in books.items()}
    with gzip.open(P/'inputs/books.pickle.gz','wb') as f:pickle.dump(data,f,protocol=5)
    s.save('book_sources.json',sources);print('BOOKS',len(data),sum(map(len,data.values())),flush=True)
    return data
def load():
    if not (P/'inputs/books.pickle.gz').exists():return prepare()
    return pickle.load(gzip.open(P/'inputs/books.pickle.gz','rb'))

@njit(cache=True)
def replay(x,result,start,close,bb,ec,ex,continuous=True,latency=.25,eligible=None):
    policy,rem,band=int(ec[0]),int(ec[1]),int(ec[2]);mode,amount,activate,confirm,arm,col,margin=ex
    found=False;ej=-1;side=0;entry_price=0.;ei=-1;fallback=0
    for j in range(0 if band else 14-rem,14):
        if eligible is not None and not eligible[j]:continue
        b,a=x[j,0],x[j,1]
        if math.isnan(b+a) or not 0<=b<=a<=1 or a-b>.05000001:continue
        side,fallback=direction(x[j],policy);ask=a if side==1 else 1-b
        if band and 14-j>rem and not .55-1e-8<=ask<=.65+1e-8:continue
        T=start+60*(j+1)
        if T<bb[0,0]:continue
        k=np.searchsorted(bb[:,0],T+3+latency)
        while k<len(bb) and bb[k,0]<=min(T+35,close-.01):
            row=bb[k];yb,nb=row[1],row[4];vwap=row[5] if side==1 else row[2]
            if not math.isnan(yb+nb+vwap) and -.00000001<=1-yb-nb<=.05000001 and 0<1-vwap<1:
                found=True;ej=j;entry_price=1-vwap;ei=k;break
            k+=1
        if found:break
    if not found:return -1,0,0.,-1,0.,False,-1,0,0.
    if mode==0:return ej,side,entry_price,len(bb),1. if result==side else 0.,False,-1,fallback,bb[ei,0]
    peak=entry_price;armed=False;trigger=-1;breach_start=-1.;next_observe=start+60*(ej+2)+3
    for k in range(ei+1,len(bb)):
        row=bb[k];now=row[0]
        if now>=close:break
        bid=row[2] if side==1 else row[5]
        if math.isnan(bid+row[1]+row[4]) or row[1]+row[4]>1.00000001:
            if trigger<0:breach_start=-1.
            continue
        if trigger<0 and (continuous or now>=next_observe):
            next_observe=start+60*(math.floor((now-start-3)/60)+1)+3
            fj=max(0,min(13,int((now-start-3)//60)-1));peak=max(peak,bid)
            if bid>=entry_price+arm-1e-8:armed=True
            remaining=(close-now)/60
            hit=breach(int(mode),amount,activate,arm,int(col),margin,remaining,14-ej,bid,entry_price,peak,armed,x[fj],side)
            if hit:
                if breach_start<0:breach_start=now
                if now-breach_start>=(confirm-1)*60-1e-8:trigger=k
            else:breach_start=-1.
        if trigger>=0 and now>=bb[trigger,0]+latency and 0<bid<1:return ej,side,entry_price,k,bid,True,trigger,fallback,bb[ei,0]
    return ej,side,entry_price,len(bb),1. if result==side else 0.,False,trigger,fallback,bb[ei,0]

def run():
    books=load();x,mm=s.load();f=json.loads((P/'frozen_selection.json').read_text());cc=json.loads((P/'configurations.json').read_text());out={}
    for cid in f['selected_ids']:
        c=cc[cid]
        for continuous,latency in ((False,.25),(True,.25),(True,3.)):
            rr=[];den=collections.Counter()
            for i,m in enumerate(mm):
                bb=books.get(m['ticker']);ci=s.co(m['close'])
                if bb is None or bb[-1,0]<m['close']-35:continue
                den[ci]+=1;r=replay(x[i],m['result'],m['start'],m['close'],bb,s.earray(c['entry']),s.xarray(c['exit']),continuous,latency)
                j,side,price,k,xp,sold,trig,fallback,entered=r
                if j<0:continue
                T=m['start']+60*(j+1);debit=float(cash(price,True));credit=float(cash(xp,False)) if sold else xp;won=int(side==m['result']);xt=bb[k,0] if sold else max(m['close']+1,m['settled'])
                rr.append(dict(ticker=m['ticker'],cohort=s.PERIODS[ci],close=m['close'],entered=entered,decision=T,entry_index=int(j),side=int(side),entry=price,exit=xp,exit_ts=float(xt),sold=bool(sold),trigger_index=int(trig),armed=False,forecast_fallback=bool(fallback),debit=debit,credit=credit,legs=1+int(sold),settlement_win=won,pnl=debit+credit,hold_pnl=debit+won))
            key=f'{cid}_continuous{int(continuous)}_delay{latency}'
            with gzip.open(P/f'book_trades_{key}.json.gz','wt') as file:json.dump(rr,file,separators=(',',':'))
            periods={s.PERIODS[ci]:dict(base=s.metrics([r for r in rr if r['cohort']==s.PERIODS[ci]],den[ci],ci),cost1c=s.metrics([r for r in rr if r['cohort']==s.PERIODS[ci]],den[ci],ci,.01)) for ci in den}
            out[key]=dict(config=c,continuous=continuous,latency=latency,periods=periods)
            if continuous and latency==.25:print('BOOK',cid,[(co,v['base']['trades'],v['base']['net']) for co,v in periods.items()],flush=True)
    s.save('book_results.json',out)
if __name__=='__main__':run()
