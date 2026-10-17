import json,gzip,calendar,math,numpy as np,sys
D='../kalshi_perp_flow_minute_2026_10_10/data/'
ASSETS=['KXBTCPERP','KXETHPERP','KXSOLPERP','KXXRPPERP'];HOLD=calendar.timegm((2026,9,1,0,0,0))
def load(t):
    d=json.load(gzip.open(D+f'{t}_1m.json.gz','rt'))['rows'];t0=min(map(int,d));n=(max(map(int,d))-t0)//60+1
    bid=np.full(n,np.nan);ask=np.full(n,np.nan)
    for k,v in d.items():
        i=(int(k)-t0)//60
        if v[0] and v[1] and v[1]>=v[0]>0 and v[1]/v[0]<1.02: bid[i]=v[0];ask[i]=v[1]
    mid=(bid+ask)/2
    # neighbour sanity: drop minutes >3% from rolling median of +-30
    med=np.array([np.nanmedian(mid[max(0,i-30):i+31]) for i in range(n)]) if n<0 else None
    ref=np.copy(mid);
    for s in range(0,n,1440):
        w=mid[max(0,s-60):s+1500];m=np.nanmedian(w)
        bad=np.abs(mid[s:s+1440]/m-1)>0.15
        bid[s:s+1440][bad]=np.nan;ask[s:s+1440][bad]=np.nan
    jump=np.zeros(n,bool);jump[1:-1]=(np.abs(mid[1:-1]/mid[:-2]-1)>0.03)&(np.abs(mid[1:-1]/mid[2:]-1)>0.03)
    bid[jump]=np.nan;ask[jump]=np.nan;mid=(bid+ask)/2
    return t0+60*np.arange(n),bid,ask,mid
def tp(x,q,lb=10080,step=60):
    o=np.full(len(x),np.nan)
    for i in range(lb,len(x),step):
        w=x[i-lb:i];w=w[np.isfinite(w)]
        if len(w)>lb/2:o[i:i+step]=np.percentile(w,q)
    return o
RULES=['LONG']+[f'{a}{k}' for a in ('MOM','REV') for k in (5,15,60)]+[f'STRONG_{a}{k}' for a in ('MOM','REV') for k in (5,15,60)]
res={r:{c:{'D':[],'O':[]} for c in ('GROSS','CB','KALSHI','CB_INSTANT')} for r in RULES};per={r:{t:{'D':[],'O':[]} for t in ASSETS} for r in RULES}
print('5-minute move size (mid, all rounds) vs round-trip costs:')
for t in ASSETS:
    ts,bid,ask,mid=load(t);n=len(mid)
    rets={k:np.full(n,np.nan) for k in (5,15,60)}
    for k in (5,15,60): rets[k][k:]=mid[k:]/mid[:-k]-1
    thr={k:tp(np.abs(rets[k]),90) for k in (5,15,60)}
    mv=[];spr=[]
    for i in range(60,n-7):
        if ts[i]%300: continue
        e,x=i+1,i+6
        if not (np.isfinite(mid[e]) and np.isfinite(mid[x]) and np.isfinite(mid[i])): continue
        g=(mid[x]/mid[e]-1);mv.append(abs(g)*1e4);spr.append((ask[e]-bid[e])/mid[e]*1e4)
        seg='O' if ts[i]>=HOLD else 'D'
        for r in RULES:
            if r=='LONG': s=1
            else:
                st=r.startswith('STRONG_');base=r.replace('STRONG_','');k=int(base[3:]);v=rets[k][i]
                if not np.isfinite(v) or v==0: continue
                if st and not (np.isfinite(thr[k][i]) and abs(v)>=thr[k][i]): continue
                s=np.sign(v)*(1 if base.startswith('MOM') else -1)
            pin=ask[e] if s>0 else bid[e];pout=bid[x] if s>0 else ask[x]
            cross=s*(pout-pin)/pin
            res[r]['GROSS'][seg].append(s*g*1e4);res[r]['CB'][seg].append((cross-6e-4)*1e4);res[r]['KALSHI'][seg].append((cross-24e-4)*1e4)
            pi2=ask[i] if s>0 else bid[i];res[r]['CB_INSTANT'][seg].append((s*(pout-pi2)/pi2-6e-4)*1e4)
            per[r][t][seg].append((cross-6e-4)*1e4)
    mv=np.array(mv);spr=np.array(spr)
    print(f"  {t:10} rounds={len(mv)} median|move|={np.median(mv):.1f}bp p75={np.percentile(mv,75):.1f} p90={np.percentile(mv,90):.1f}  median spread={np.median(spr):.2f}bp  | rounds with |move| > CB RT (6bp+spread): {np.mean(mv>6+spr)*100:.0f}%  > Kalshi RT (24bp+spread): {np.mean(mv>24+spr)*100:.0f}%")
def st(v):
    v=np.array(v);return f"n={len(v):6d} {v.mean():+6.2f}bp t={v.mean()/(v.std(ddof=1)/math.sqrt(len(v))):+6.2f}" if len(v)>2 else 'n/a'
print(f"\n{'rule':16}{'GROSS disc':>30}{'CB disc':>30}{'CB holdout':>30}{'KALSHI disc':>30}")
sel=[]
for r in RULES:
    print(f"{r:16}{st(res[r]['GROSS']['D']):>30}{st(res[r]['CB']['D']):>30}{st(res[r]['CB']['O']):>30}{st(res[r]['KALSHI']['D']):>30}")
    v=np.array(res[r]['CB']['D'])
    if len(v)>2 and v.mean()>0 and v.mean()/(v.std(ddof=1)/math.sqrt(len(v)))>=3: sel.append(r)
print('\nSELECTED (CB discovery mean>0, t>=3):',sel or 'none')
print('\nOptimistic instant-entry CB, discovery (sensitivity only):')
for r in RULES: print(f"  {r:16}{st(res[r]['CB_INSTANT']['D'])}")
json.dump({r:{c:{s:[float(x) for x in res[r][c][s]] for s in 'DO'} for c in res[r]} for r in RULES},gzip.open('results.json.gz','wt'))
