import json,gzip,calendar,time,numpy as np
CS={'KXBTCPERP':1e-4,'KXXRPPERP':1.0};SPOT={'KXBTCPERP':'BTC-USD','KXXRPPERP':'XRP-USD'};LMAX={'KXBTCPERP':6.3858,'KXXRPPERP':2.8833}
PT=12e-4;CAP=10000.0;IR=0.0325
def P(s): return calendar.timegm(time.strptime(s,'%Y-%m-%dT%H:%M:%SZ'))
def load(t):
    d=json.load(gzip.open(f'data/{t}_1m.json.gz','rt'))['rows']
    m={int(k):v for k,v in d.items() if v[0] and v[1] and v[1]>=v[0]>0}
    sp={r[0]+3600:r[4]*CS[t] for r in json.load(open(f'data/{SPOT[t]}_1h.json'))}  # close at end of hour, per-contract units
    bad=0
    for k in list(m):
        ref=sp.get((k//3600+1)*3600) or sp.get(k//3600*3600)
        mid=(m[k][0]+m[k][1])/2
        if not ref or abs(mid/ref-1)>0.03: del m[k]; bad+=1
    print(t,'minutes dropped by spot sanity filter (|perp mid/Coinbase-1|>3% or no ref):',bad,'of',bad+len(m))
    fu=[(P(x['funding_time']),float(x['funding_rate']),float(x['mark_price'])) for x in json.load(open(f'data/{t}_funding.json'))]
    return m,sp,fu
def near(m,ts,back=600):
    for k in range(0,back,60):
        if ts-k in m: return m[ts-k]
    return None
def episode(t,m,sp,fu,t0,t1,L,MMf,fs,topup):
    """carry episode t0->t1. Wealth accounting: result = end wealth - CAP. Adverse path = filtered minute ask high
    (kept only if <= 1.02x ask close, else ask close) -- conservative vs Kalshi's index mark."""
    q0=near(m,t0);S0=sp.get(t0)
    if not q0 or not S0: return None
    pin=q0[0]
    N=CAP/(S0*(1+fs)+pin/L+pin*PT)
    units=N; margin=N*pin/L; fee_tot=N*pin*PT+N*S0*fs
    fund=0.0;topups=0;liq=None;minratio=9;topcash=0.0
    fi=[f for f in fu if t0<f[0]<=t1];fptr=0
    keys=sorted(k for k in m if t0<k<=t1)
    MMa=MMf/LMAX[t]
    for k in keys:
        while fptr<len(fi) and fi[fptr][0]<=k:
            _,r,mk=fi[fptr]; fund+=r*mk*N; margin+=r*mk*N; fptr+=1
        ac=m[k][1]; ah=m[k][5] if (m[k][5] and m[k][5]<=1.02*ac) else ac
        eq=margin+N*(pin-ah)
        minratio=min(minratio,eq/(N*ah/L))
        if eq<=MMa*N*ah: liq=k;break
        if topup and k%86400==0 and sp.get(k):
            eqm=margin+N*(pin-ac); req=N*ac/L
            if eqm<0.5*req:
                add=req-eqm; sell=add/(sp[k]*(1-fs)); units-=sell; fee_tot+=sell*sp[k]*fs; margin+=add; topcash+=add; topups+=1
    tend=liq if liq else t1
    hx=(tend//3600+1)*3600 if liq else t1
    Sx=sp.get(hx) or sp.get(hx-3600)
    if liq: perp_eq=0.0
    else:
        px=near(m,t1)[1]; perp_eq=margin+N*(pin-px)-N*px*PT; fee_tot+=N*px*PT
    spot_val=units*Sx*(1-fs); fee_tot+=units*Sx*fs
    wealth=perp_eq+spot_val   # topup cash went spot->margin, already inside both
    days=(tend-t0)/86400
    interest=IR*(N*pin/L)*days/365
    days=(t1-t0)/86400
    return dict(days=days,fund=fund,fees=fee_tot,total=wealth-CAP,liq=liq,topups=topups,minratio=minratio,notional=N*pin,interest=interest,
                basis=wealth-CAP-fund+fee_tot if not liq else None)
def ann(x,days): return x/CAP*365/days*100 if days>0 else float('nan')
out={}
for t in ['KXBTCPERP','KXXRPPERP']:
    m,sp,fu=load(t)
    starts={'FULL':calendar.timegm((2026,6,4,0,0,0)),'HOLDOUT':calendar.timegm((2026,9,1,0,0,0))}
    END=calendar.timegm((2026,10,10,20,0,0))
    Ls=[L for L in [1,2,3,5] if L<LMAX[t]]+[round(LMAX[t],2)]
    print(f'\n######## {t}  (capital ${CAP:,.0f}; spot fee per side as column; perp taker 12bp; MM bracket a=0.5/Lmax b=1/Lmax)')
    for per,t0 in starts.items():
        print(f'--- static hold {per}: {time.strftime("%Y-%m-%d",time.gmtime(t0))} -> {time.strftime("%Y-%m-%d",time.gmtime(END))}')
        print(f"{'L':>5}{'MM':>4}{'topup':>6}{'fee':>6}{'total$':>9}{'ann%':>7}{'+int ann%':>10}{'fund$':>8}{'basis$':>8}{'fees$':>7}{'liq':>18}{'topups':>7}{'minMR':>7}")
        for L in Ls:
            for MMf,mml in ((0.5,'a'),(1.0,'b')):
                for topup in (False,True):
                    for fs in (0.001,0.004,0.006):
                        r=episode(t,m,sp,fu,t0,END,L,MMf,fs,topup)
                        if not r: continue
                        out[f'{t}|{per}|L{L}|{mml}|{topup}|{fs}']=r
                        if fs!=0.004 and not (mml=='a' and not topup): continue
                        print(f"{L:5}{mml:>4}{'P1' if topup else 'P0':>6}{fs*100:6.1f}{r['total']:9.0f}{ann(r['total'],r['days']):7.1f}{ann(r['total']+r['interest'],r['days']):10.1f}{r['fund']:8.0f}{(r['basis'] if r['basis'] is not None else float('nan')):8.0f}{r['fees']:7.0f}{(time.strftime('%m-%d %H:%M',time.gmtime(r['liq'])) if r['liq'] else '-'):>18}{r['topups']:7}{r['minratio']:7.2f}")
    # rule schedule: ON when trailing-7d ann funding >=8%, OFF when <=2%
    print(f'--- RULE on>=8% / off<=2% trailing-7d (full period, MM a, P1, spot fee 0.4%) L=1 and L=3 (BTC) / 2 (XRP)')
    for L in ((1,3) if t=="KXBTCPERP" else (1,2)):
        on=None;eps=[]
        for i,(ft,r,mk) in enumerate(fu):
            w=[x[1] for x in fu if ft-7*86400<x[0]<=ft]; a=sum(w)*365/7*100 if len(w)>=18 else None
            if a is None: continue
            if on is None and a>=8: on=(ft//3600+1)*3600
            elif on is not None and a<=2: eps.append((on,(ft//3600+1)*3600)); on=None
        if on: eps.append((on,END))
        tot=0;days=0;liqs=0;fund=0
        for a,b in eps:
            r=episode(t,m,sp,fu,a,b,L,0.5,0.004,True)
            if r: tot+=r['total'];days+=r['days'];liqs+=bool(r['liq']);fund+=r['fund']
        span=(END-starts['FULL'])/86400
        print(f"  L={L}: episodes={len(eps)} days_on={days:.0f}/{span:.0f} total=${tot:.0f} funding=${fund:.0f} ann_on_capital_over_span={tot/CAP*365/span*100:.1f}% liqs={liqs}")
json.dump(out,open('study_b_results.json','w'),default=float)
