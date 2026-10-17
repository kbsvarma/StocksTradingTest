import json,urllib.request,urllib.parse,time,calendar,statistics
B='https://external-api.kalshi.com/trade-api/v2'
def get(p,q):
    with urllib.request.urlopen(urllib.request.Request(B+p+'?'+urllib.parse.urlencode(q),headers={'User-Agent':'research'}),timeout=30) as r: return json.load(r)
S=calendar.timegm((2026,7,28,0,0,0));E=int(time.time())
out={}
print(f"{'ticker':12}{'n':>4}{'pos%':>6}{'zero%':>6}{'ann%full':>9}  weekly ann% (oldest->newest, 7d blocks)")
for t in ['KXBTCPERP','KXETHPERP','KXSOLPERP','KXXRPPERP','KXLINKPERP','KXUNIPERP','KXZECPERP','KXHYPEPERP','KXDOGEPERP','KXSUIPERP','KXGOLDPERP','KXSILVERPERP']:
    fr=[];s=S
    while s<E:
        e=min(s+15*86400,E); fr+=get('/margin/funding_rates/historical',{'ticker':t,'start_ts':s,'end_ts':e}).get('funding_rates',[]); s=e; time.sleep(.15)
    d={x['funding_time']:float(x['funding_rate']) for x in fr}; ks=sorted(d); r=[d[k] for k in ks]; out[t]=[[k,d[k]] for k in ks]
    if not r: print(t,'none'); continue
    days=(calendar.timegm(time.strptime(ks[-1],'%Y-%m-%dT%H:%M:%SZ'))-calendar.timegm(time.strptime(ks[0],'%Y-%m-%dT%H:%M:%SZ')))/86400 or 1
    wk=[];ts=[calendar.timegm(time.strptime(k,'%Y-%m-%dT%H:%M:%SZ')) for k in ks]
    b=E-7*86400
    while b>ts[0]-1:
        w=[r[i] for i in range(len(r)) if b<=ts[i]<b+7*86400]; wk.append(sum(w)*365/7*100); b-=7*86400
    print(f"{t:12}{len(r):4}{sum(x>0 for x in r)/len(r)*100:6.0f}{sum(x==0 for x in r)/len(r)*100:6.0f}{sum(r)*365/days*100:9.1f}  "+' '.join(f'{x:5.0f}' for x in reversed(wk)))
json.dump(out,open('data/funding_since_0728.json','w'))
