import json,urllib.request,time,calendar,numpy as np
S=calendar.timegm((2026,7,28,0,0,0));E=int(time.time())
def hl(coin):
    out=[];st=S*1000
    while st<E*1000:
        req=urllib.request.Request('https://api.hyperliquid.xyz/info',data=json.dumps({'type':'fundingHistory','coin':coin,'startTime':st}).encode(),headers={'Content-Type':'application/json'})
        d=json.load(urllib.request.urlopen(req,timeout=30))
        if not d: break
        out+=d; nst=d[-1]['time']+1
        if nst<=st: break
        st=nst; time.sleep(.3)
    return [(r['time']//1000,float(r['fundingRate'])) for r in out]
def kal(t):
    f=json.load(open(f'data/{t}_funding.json'))
    return [(calendar.timegm(time.strptime(x['funding_time'],'%Y-%m-%dT%H:%M:%SZ')),float(x['funding_rate'])) for x in f]
res={}
print(f"{'coin':5}{'HL ann%':>9}{'Kalshi ann%':>12}{'HL-K ann%':>10}   weekly HL-K ann% (oldest->newest)   | 8h-aligned spread: mean, sd, %prints HL>K")
for c,t in (('BTC','KXBTCPERP'),('ETH','KXETHPERP'),('SOL','KXSOLPERP'),('XRP','KXXRPPERP')):
    h=hl(c);k=[x for x in kal(t) if x[0]>=S]
    days=(E-S)/86400
    ha=sum(r for _,r in h)*365/days*100; ka=sum(r for _,r in k)*365/days*100
    # align: HL hourly summed over each Kalshi 8h period ending at kalshi print
    hd=dict(h);sp=[]
    for ts,r in k:
        hs=sum(hd.get(ts-3600*i,0) for i in range(8)); sp.append((ts,hs-r))
    sp=np.array([x[1] for x in sp]); wk=[]
    b=E-7*86400
    while b>S:
        wk.append((sum(r for ts,r in h if b<=ts<b+7*86400)-sum(r for ts,r in k if b<=ts<b+7*86400))*365/7*100); b-=7*86400
    print(f"{c:5}{ha:9.1f}{ka:12.1f}{ha-ka:10.1f}   "+' '.join(f'{x:5.1f}' for x in reversed(wk))+f"   | {sp.mean()*1e4:+.2f}bp/8h sd {sp.std()*1e4:.2f}bp  {np.mean(sp>0)*100:.0f}%")
    res[c]={'hl':h,'kalshi':k}
json.dump(res,open('data/hl_vs_kalshi_funding.json','w'))
