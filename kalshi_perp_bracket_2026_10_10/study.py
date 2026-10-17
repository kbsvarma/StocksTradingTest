import json,gzip,calendar,math,numpy as np,numba,itertools
D='../kalshi_perp_flow_minute_2026_10_10/data/';ASSETS=['KXBTCPERP','KXETHPERP','KXSOLPERP','KXXRPPERP'];HOLD=calendar.timegm((2026,9,1,0,0,0))
def load(t):
    d=json.load(gzip.open(D+f'{t}_1m.json.gz','rt'))['rows'];t0=min(map(int,d));n=(max(map(int,d))-t0)//60+1
    A=np.full((n,6),np.nan)  # bid ask bid_lo ask_lo bid_hi ask_hi
    for k,v in d.items():
        i=(int(k)-t0)//60
        if v[0] and v[1] and v[1]>=v[0]>0 and v[1]/v[0]<1.02:
            A[i,0]=v[0];A[i,1]=v[1]
            for j,c in ((2,2),(3,3),(4,4),(5,5)):
                x=v[c]
                if x and 0.98*v[0]<=x<=1.02*v[1]: A[i,j]=x
                else: A[i,j]=v[0] if j in (2,4) else v[1]
    mid=(A[:,0]+A[:,1])/2
    jump=np.zeros(n,bool);jump[1:-1]=(np.abs(mid[1:-1]/mid[:-2]-1)>0.03)&(np.abs(mid[1:-1]/mid[2:]-1)>0.03);A[jump]=np.nan
    return t0+60*np.arange(n),A
@numba.njit
def run(A,mom,TP,SL,mode,fe,fm,fs,pess):
    n=A.shape[0];out=np.zeros((200000,5));m=0;i=16
    while i<n-2:
        e=i+1
        if np.isnan(A[e,0]) or np.isnan(A[i,0]): i+=1;continue
        if mode==0: s=1.0
        elif mode==1: s=-1.0
        else:
            v=mom[i]
            if np.isnan(v) or v==0: i+=1;continue
            s=np.sign(v)*(1.0 if mode==2 else -1.0)
        pin=A[e,1] if s>0 else A[e,0]
        tp=pin*(1+s*TP);sl=pin*(1-s*SL);j=e+1;res=0.0;ext=-1;hit=0
        while j<n:
            if np.isnan(A[j,0]): j+=1;continue
            if s>0:
                if A[j,2]<=sl:
                    px=min(sl,A[j,2]) if pess else sl; res=(px-pin)/pin-fe-fs;hit=-1;break
                if A[j,4]>tp: res=(tp-pin)/pin-fe-fm;hit=1;break
            else:
                if A[j,5]>=sl:
                    px=max(sl,A[j,5]) if pess else sl; res=(pin-px)/pin-fe-fs;hit=-1;break
                if A[j,3]<tp: res=(pin-tp)/pin-fe-fm;hit=1;break
            j+=1
        if j>=n: break
        gross=res+fe+(fm if hit==1 else fs)
        out[m,0]=i;out[m,1]=res;out[m,2]=gross;out[m,3]=hit;out[m,4]=j-e;m+=1
        i=j
    return out[:m]
def st(v):
    v=np.asarray(v);return (v.mean(),v.mean()/(v.std(ddof=1)/math.sqrt(len(v)))) if len(v)>2 else (np.nan,np.nan)
data={t:load(t) for t in ASSETS};MOM={}
for t,(ts,A) in data.items():
    mid=(A[:,0]+A[:,1])/2;r=np.full(len(mid),np.nan);r[15:]=mid[15:]/mid[:-15]-1;MOM[t]=r
TPS=[10,20,40,80,160];SLS=TPS;DIRS=['LONG','SHORT','MOM15','REV15']
rows=[]
for (fname,fe,fm,fs),pess in itertools.product((('CB',3e-4,1e-4,3e-4),('KALSHI',12e-4,2e-4,12e-4)),(False,True)):
    if fname=='KALSHI' and pess: continue
    for d,TP,SL in itertools.product(range(4),TPS,SLS):
        seg={'D':[], 'O':[]};g={'D':[],'O':[]};hits={'D':[],'O':[]};per={t:{'D':[],'O':[]} for t in ASSETS};dur=[]
        for t in ASSETS:
            ts,A=data[t];o=run(A,MOM[t],TP*1e-4,SL*1e-4,d,fe,fm,fs,pess)
            for row in o:
                k='O' if ts[int(row[0])]>=HOLD else 'D'
                seg[k].append(row[1]*1e4);g[k].append(row[2]*1e4);hits[k].append(row[3]>0);per[t][k].append(row[1]*1e4);dur.append(row[4])
        mD,tD=st(seg['D']);mO,tO=st(seg['O'])
        rows.append(dict(fees=fname,pess=pess,dir=DIRS[d],TP=TP,SL=SL,nD=len(seg['D']),mD=mD,tD=tD,nO=len(seg['O']),mO=mO,tO=tO,
            gD=float(np.mean(g['D'])),gO=float(np.mean(g['O'])),hitD=float(np.mean(hits['D'])),hitO=float(np.mean(hits['O'])),bench=SL/(TP+SL),
            medmin=float(np.median(dur)),assetsO=sum(1 for t in ASSETS if per[t]['O'] and np.mean(per[t]['O'])>0)))
json.dump(rows,open('results.json','w'),default=float)
cb=[r for r in rows if r['fees']=='CB' and not r['pess']]
print('=== CB fees, optimistic stop fill. Sorted by discovery t. hit = TP hit rate; bench = SL/(TP+SL) (zero-edge expectation)')
print(f"{'dir':6}{'TP':>4}{'SL':>4}{'nD':>7}{'grossD':>8}{'netD':>8}{'tD':>7}{'hitD':>6}{'bench':>6}{'nO':>6}{'grossO':>8}{'netO':>8}{'tO':>7}{'med_min':>8}")
for r in sorted(cb,key=lambda r:-r['tD'])[:25]+[None]+sorted(cb,key=lambda r:r['tD'])[:5]:
    if r is None: print('   ...');continue
    print(f"{r['dir']:6}{r['TP']:4}{r['SL']:4}{r['nD']:7}{r['gD']:8.2f}{r['mD']:8.2f}{r['tD']:7.2f}{r['hitD']:6.3f}{r['bench']:6.3f}{r['nO']:6}{r['gO']:8.2f}{r['mO']:8.2f}{r['tO']:7.2f}{r['medmin']:8.0f}")
gross=np.array([r['gD'] for r in cb]);excess=np.array([r['hitD']-r['bench'] for r in cb])
print(f"\nAll 100 CB cells: gross/trade discovery median {np.median(gross):+.2f}bp (range {gross.min():+.1f}..{gross.max():+.1f}); TP-hit minus zero-edge benchmark median {np.median(excess)*100:+.2f}pp")
sel=[r for r in cb if r['mD']>0 and r['tD']>=3]
print('SELECTED (CB optimistic, disc mean>0 & t>=3):',len(sel))
for r in sel:
    opp={'LONG':'SHORT','SHORT':'LONG','MOM15':'REV15','REV15':'MOM15'}[r['dir']]
    o=[x for x in cb if x['dir']==opp and x['TP']==r['TP'] and x['SL']==r['SL']][0]
    pes=[x for x in rows if x['fees']=='CB' and x['pess'] and x['dir']==r['dir'] and x['TP']==r['TP'] and x['SL']==r['SL']][0]
    ok=r['mO']>0 and r['tO']>=2 and r['assetsO']>=3 and not (o['mD']>0 and o['tD']>=2)
    print(f"  {r['dir']} TP{r['TP']} SL{r['SL']}: holdout {r['mO']:+.2f}bp t={r['tO']:.2f} assets+ {r['assetsO']}/4 | opposite {opp} disc {o['mD']:+.2f} t={o['tD']:.2f} | pessimistic-SL disc {pes['mD']:+.2f} hold {pes['mO']:+.2f} -> {'PASS' if ok else 'FAIL'}")
kb=[r for r in rows if r['fees']=='KALSHI'];print(f"\nKALSHI fees: cells with discovery mean>0: {sum(r['mD']>0 for r in kb)}/100; best {max(r['mD'] for r in kb):+.2f}bp")
