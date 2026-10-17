import json,urllib.request,urllib.parse,time,calendar,gzip,numpy as np
def g(u,q):
    with urllib.request.urlopen(urllib.request.Request(u+'?'+urllib.parse.urlencode(q),headers={'User-Agent':'research'}),timeout=30) as r: return json.load(r)
S=calendar.timegm((2026,7,28,0,0,0));E=int(time.time())//3600*3600
out={}
for pid,kt,cs,spot in (('BIP-20DEC30-CDE','KXBTCPERP',1e-4,'BTC-USD'),('ETP-20DEC30-CDE','KXETHPERP',None,None),('XPP-20DEC30-CDE','KXXRPPERP',1.0,'XRP-USD'),('SLP-20DEC30-CDE','KXSOLPERP',None,None)):
    c={};s=S
    while s<E:
        e=min(s+300*3600,E)
        try:
            for x in g(f'https://api.coinbase.com/api/v3/brokerage/market/products/{pid}/candles',{'start':s,'end':e,'granularity':'ONE_HOUR'}).get('candles',[]): c[int(x['start'])+3600]=float(x['close'])
        except Exception as ex: print(pid,'ERR',ex); break
        s=e; time.sleep(.3)
    k=json.load(gzip.open(f'data/{kt}_1m.json.gz','rt'))['rows']
    kcs={'KXBTCPERP':1e-4,'KXETHPERP':None,'KXXRPPERP':1.0,'KXSOLPERP':None}[kt]
    # Kalshi contract size lookup from saved markets
    ms={m['ticker']:m for m in json.load(open('../kalshi_perp_closing_flow_2026_10_10/data/markets.json'))['markets']}
    ksz=float(ms[kt]['contract_size'])
    b=[]
    for T,cp in c.items():
        v=k.get(str(T)) or k.get(str(T-60))
        if not v or not v[0] or not v[1]: continue
        km=(v[0]+v[1])/2/ksz
        if abs(km/cp-1)>0.03: continue
        b.append((T,(km/cp-1)*1e4))
    b.sort(); arr=np.array([x[1] for x in b])
    if len(arr)==0: print(pid,'no overlap',len(c)); continue
    print(f"{kt:10} vs {pid}: hours={len(arr)} Kalshi-minus-CDE premium median {np.median(arr):+.2f}bp mean {arr.mean():+.2f}bp p10 {np.percentile(arr,10):+.2f} p90 {np.percentile(arr,90):+.2f}  share>0 {np.mean(arr>0)*100:.0f}%  (CDE candles: last trade close; Kalshi: bid/ask mid)")
    out[kt]=b
json.dump(out,open('data/kalshi_vs_cde_basis.json','w'))
# current funding snapshot
d=json.load(open('/private/tmp/claude-501/-Users-varmakammili-Documents-GitHub-StocksTradingTest/aa41ed74-dfc7-491b-a482-eeb824d3eec9/scratchpad/cb_futures_all.json'))['products']
for p in d:
    if p['product_id'] in ('BIP-20DEC30-CDE','ETP-20DEC30-CDE','XPP-20DEC30-CDE','SLP-20DEC30-CDE'):
        f=p['future_product_details'];print(p['product_id'],'current hourly funding',f['funding_rate'],'=> ann',round(float(f['funding_rate'])*8760*100,1),'%  OI',f.get('open_interest'),' 24h $vol',p.get('approximate_quote_24h_volume'))
