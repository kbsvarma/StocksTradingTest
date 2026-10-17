import json,urllib.request,urllib.parse,time,math,statistics,calendar,os,sys
B='https://external-api.kalshi.com/trade-api/v2'
def get(p,q):
    for k in range(4):
        try:
            with urllib.request.urlopen(urllib.request.Request(B+p+'?'+urllib.parse.urlencode(q),headers={'User-Agent':'research'}),timeout=30) as r: return json.load(r)
        except Exception as e: time.sleep(2+k*3); err=e
    raise err
START=calendar.timegm((2026,7,28,0,0,0)); END=int(time.time())//3600*3600
def hourly(t):
    f=f'data/{t}_1h.json'
    if os.path.exists(f): return json.load(open(f))
    out=[];s=START-4*3600
    while s<END:
        e=min(s+20*86400,END); out+=get(f'/margin/markets/{t}/candlesticks',{'start_ts':s,'end_ts':e,'period_interval':60})['candlesticks']; s=e; time.sleep(.2)
    d={c['end_period_ts']:c for c in out}; out=[d[k] for k in sorted(d)]; json.dump(out,open(f,'w')); return out
mcache={}
def minute(t,ts):  # 1-min candle ending ts
    f=f'data/{t}_1m.json'
    if not mcache.get(t): mcache[t]=json.load(open(f)) if os.path.exists(f) else {}
    k=str(ts)
    if k not in mcache[t]:
        cs=get(f'/margin/markets/{t}/candlesticks',{'start_ts':ts-120,'end_ts':ts+60,'period_interval':1})['candlesticks']
        c=[x for x in cs if x['end_period_ts']==ts]; mcache[t][k]=c[0] if c else None; time.sleep(.15)
    return mcache[t][k]
def q(c):
    try: b=float(c['bid']['close']);a=float(c['ask']['close'])
    except Exception: return None
    return (b,a) if b>0 and a>=b else None
def run(t,cond):
    H={c['end_period_ts']:c for c in hourly(t)}; trades=[];busy=0
    for T in sorted(H):
        if T<START or T<busy or T+6*3600+60>END: continue
        c0,c3=H.get(T),H.get(T-3*3600)
        if not c3: continue
        q0,q3=q(c0),q(c3)
        try: oi0=float(c0['open_interest']);oi3=float(c3['open_interest'])
        except Exception: continue
        if not q0 or not q3: continue
        m0=sum(q0)/2;m3=sum(q3)/2;r3=m0/m3-1;doi=oi0-oi3
        ok=(doi<0) if cond=='down' else (doi>0) if cond=='up' else True
        if abs(r3)<0.01 or not ok: continue
        ei=minute(t,T+60);ex=minute(t,T+6*3600+60)
        qi,qx=q(ei) if ei else None,q(ex) if ex else None
        if not qi or not qx: trades.append({'ts':T,'missing':1}); busy=T+6*3600; continue
        s=-1 if r3>0 else 1; mi=sum(qi)/2; mx=sum(qx)/2
        gross=s*(mx-mi)/mi*1e4
        pin=qi[1] if s==1 else qi[0]; pout=qx[0] if s==1 else qx[1]
        execbp=s*(pout-pin)/pin*1e4-24
        trades.append({'ts':T,'side':s,'r3':r3,'doi':doi,'gross':gross,'exec':execbp,'maker':gross-4}); busy=T+6*3600
    json.dump(mcache.get(t,{}),open(f'data/{t}_1m.json','w'))
    return trades
def stats(x):
    n=len(x)
    if n<2: return f'n={n}'
    m=statistics.mean(x);sd=statistics.stdev(x);return f'n={n:3d} mean={m:+7.1f}bp t={m/(sd/math.sqrt(n)):+5.2f} win={sum(v>0 for v in x)/n:.0%}'
res={}
MID=START+(END-START)//2
for group,tks in (('CONFIRMATORY BTC+ETH',['KXBTCPERP','KXETHPERP']),('EXTENSION SOL+XRP',['KXSOLPERP','KXXRPPERP'])):
    print(f'\n##### {group}  window {time.strftime("%Y-%m-%d",time.gmtime(START))} -> {time.strftime("%Y-%m-%d %H:%M",time.gmtime(END))} UTC')
    for cond,lab in (('down','SIGNAL dOI<0'),('up','control dOI>0'),('any','control no-OI')):
        allt=[];per={}
        for t in tks:
            tr=run(t,cond);per[t]=tr;allt+=tr;res[f'{t}_{cond}']=tr
        ok=[x for x in allt if 'exec' in x];miss=len(allt)-len(ok)
        print(f'--- {lab}: missing quotes {miss}')
        print('   mid gross      ',stats([x['gross'] for x in ok]))
        print('   EXEC net (taker)',stats([x['exec'] for x in ok]))
        print('   maker best-case',stats([x['maker'] for x in ok]))
        print('   halves exec    ',stats([x['exec'] for x in ok if x['ts']<MID]),'|',stats([x['exec'] for x in ok if x['ts']>=MID]))
        for t in tks: print(f'   {t:10} exec',stats([x['exec'] for x in per[t] if 'exec' in x]))
        if cond=='down':
            e=sorted([x['exec'] for x in ok],reverse=True); print('   exec without top3: mean',f'{statistics.mean(e[3:]):+.1f}bp' if len(e)>4 else 'n/a')
json.dump(res,open('results.json','w'))
