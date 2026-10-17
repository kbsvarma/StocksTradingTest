import json,gzip,calendar,math,numpy as np,itertools,sys
ASSETS=['KXBTCPERP','KXETHPERP','KXSOLPERP','KXXRPPERP']
import os;TAKER=float(os.environ.get("TK","12"))*1e-4;MAKER=float(os.environ.get("MK","2"))*1e-4
HOLD=calendar.timegm((2026,9,1,0,0,0))
WS=[5,15,60];HS=[5,15,60,240]
def load(t):
    d=json.load(gzip.open(f'data/{t}_1m.json.gz','rt'))['rows']
    t0=min(int(k) for k in d);t1=max(int(k) for k in d);n=(t1-t0)//60+1
    A=np.full((n,8),np.nan);ts=t0+60*np.arange(n)
    for k,v in d.items(): A[(int(k)-t0)//60]=[np.nan if x is None else x for x in v]
    bid,ask=A[:,0],A[:,1];bad=~((bid>0)&(ask>=bid)&(ask/bid<1.02));A[bad,:6]=np.nan
    return ts,A
def trailing_pct(x,ts,q,lookback=10080,step=60):
    out=np.full(len(x),np.nan)
    for i in range(lookback,len(x),step):
        w=x[i-lookback:i];w=w[~np.isnan(w)]
        if len(w)>lookback*0.5: out[i:i+step]=np.percentile(w,q)
    return out
def events(ts,A):
    mid=(A[:,0]+A[:,1])/2;oi=A[:,6];ev={}
    for W in WS:
        r=np.full(len(mid),np.nan);r[W:]=mid[W:]/mid[:-W]-1
        d=np.full(len(oi),np.nan);d[W:]=oi[W:]/oi[:-W]-1
        d[~np.isfinite(d)]=np.nan
        ar=np.abs(r);pr=trailing_pct(ar,ts,95);plo=trailing_pct(d,ts,5);phi=trailing_pct(d,ts,95)
        big=ar>=pr
        for typ,cond in (('FLUSH',d<=plo),('BUILD',d>=phi)):
            idx=np.where(big&cond&np.isfinite(r))[0];keep=[];last=-10**9
            for i in idx:
                if i-last>=60: keep.append(i);last=i
            ev[(W,typ)]=[(i,np.sign(r[i])) for i in keep]
    return ev
def trade(A,i,s,H,mode):
    bid,ask,ask_lo,bid_hi=A[:,0],A[:,1],A[:,3],A[:,4]
    e=i+1
    if e+H+3>=len(A) or np.isnan(bid[e]): return None
    if mode=='TAKER':
        pin=ask[e] if s>0 else bid[e];fe=TAKER;start=e
    else:
        pin=bid[e] if s>0 else ask[e];fe=MAKER;start=None
        for k in range(e+1,e+4):
            if s>0 and ask_lo[k]<pin: start=k;break
            if s<0 and bid_hi[k]>pin: start=k;break
        if start is None: return 'unfilled'
    x=start+H
    if np.isnan(bid[x]): return None
    pout=bid[x] if s>0 else ask[x]
    return s*(pout-pin)/pin-fe-TAKER, start, x
def tstat(v):
    v=np.array(v);return (v.mean()/(v.std(ddof=1)/math.sqrt(len(v)))) if len(v)>2 else float('nan')
data={t:load(t) for t in ASSETS};EV={t:events(*data[t]) for t in ASSETS}
rows=[];trades={}
for W,typ,dr,H,mode in itertools.product(WS,['FLUSH','BUILD'],['CONT','FADE'],HS,['TAKER','MAKER']):
    key=f'W{W}_{typ}_{dr}_H{H}_{mode}';rec={'D':[],'O':[]};per={t:{'D':[],'O':[]} for t in ASSETS};unf=0;tot=0
    for t in ASSETS:
        ts,A=data[t]
        for i,sg in EV[t][(W,typ)]:
            s=sg if dr=='CONT' else -sg
            r=trade(A,i,s,H,mode);tot+=1
            if r=='unfilled': unf+=1;continue
            if r is None: continue
            seg='O' if ts[i]>=HOLD else 'D';rec[seg].append(r[0]*1e4);per[t][seg].append(r[0]*1e4)
            trades.setdefault(key,[]).append({'t':t,'ts':int(ts[i]),'seg':seg,'s':int(s),'net_bp':r[0]*1e4,'i_in':int(r[1]),'i_out':int(r[2])})
    D,O=rec['D'],rec['O']
    rows.append(dict(cell=key,nD=len(D),mD=np.mean(D) if D else np.nan,tD=tstat(D),nO=len(O),mO=np.mean(O) if O else np.nan,tO=tstat(O),
        fill=(1-unf/tot) if tot else np.nan,assetsO=sum(1 for t in ASSETS if per[t]['O'] and np.mean(per[t]['O'])>0),
        perD={t:round(float(np.mean(per[t]['D'])),1) if per[t]['D'] else None for t in ASSETS}))
rows.sort(key=lambda r:-(r['tD'] if np.isfinite(r['tD']) else -99))
print(f"{'cell':34}{'nD':>5}{'meanD':>8}{'tD':>7}{'nO':>5}{'meanO':>8}{'tO':>7}{'fill':>6}")
for r in rows: print(f"{r['cell']:34}{r['nD']:5}{r['mD']:8.1f}{r['tD']:7.2f}{r['nO']:5}{r['mO']:8.1f}{r['tO']:7.2f}{r['fill']:6.2f}")
sel=[r for r in rows if r['mD']>0 and r['tD']>=3]
print('\nSELECTED in discovery (mean>0, t>=3):',len(sel))
for r in sel:
    O=sorted([x['net_bp'] for x in trades[r['cell']] if x['seg']=='O'],reverse=True)
    pas=r['mO']>0 and r['tO']>=2 and r['assetsO']>=3 and len(O)>4 and np.mean(O[3:])>0
    print(f"  {r['cell']}: holdout n={r['nO']} mean={r['mO']:+.1f}bp t={r['tO']:.2f} assets+={r['assetsO']}/4 no-top3={np.mean(O[3:]) if len(O)>4 else float('nan'):+.1f} -> {'PASS' if pas else 'FAIL'}")
    r['PASS']=bool(pas)
json.dump({'rows':rows,'trades':trades},open('study_a_results_'+os.environ.get('TAG','kalshi')+'.json','w'),default=float)
