import json,urllib.request,urllib.parse,time,calendar,gzip,os
B='https://external-api.kalshi.com/trade-api/v2'
def get(u,q,hdr=None):
    for k in range(5):
        try:
            with urllib.request.urlopen(urllib.request.Request(u+'?'+urllib.parse.urlencode(q),headers={'User-Agent':'research'}),timeout=60) as r: return json.load(r)
        except Exception as e: err=e; time.sleep(2+3*k)
    raise err
END=int(time.time())//60*60
for t in ['KXBTCPERP','KXETHPERP','KXSOLPERP','KXXRPPERP']:
    f=f'data/{t}_1m.json.gz'
    if os.path.exists(f): continue
    s=calendar.timegm((2026,6,3,0,0,0)); rows={}
    while s<END:
        e=min(s+72*3600,END)
        for c in get(B+f'/margin/markets/{t}/candlesticks',{'start_ts':s,'end_ts':e,'period_interval':1})['candlesticks']:
            def f_(x):
                try: return float(x)
                except: return None
            rows[c['end_period_ts']]=[f_(c['bid']['close']),f_(c['ask']['close']),f_(c['bid']['low']),f_(c['ask']['low']),f_(c['bid']['high']),f_(c['ask']['high']),f_(c.get('open_interest')),f_(c.get('volume'))]
        s=e; time.sleep(.2)
    json.dump({'cols':['bid','ask','bid_lo','ask_lo','bid_hi','ask_hi','oi','vol'],'rows':{str(k):rows[k] for k in sorted(rows)}},gzip.open(f,'wt'))
    print(t,len(rows),time.strftime('%Y-%m-%d',time.gmtime(min(rows))),time.strftime('%Y-%m-%d %H:%M',time.gmtime(max(rows))))
# funding all history
for t in ['KXBTCPERP','KXXRPPERP','KXETHPERP','KXSOLPERP']:
    fr=[];s=calendar.timegm((2026,6,3,0,0,0))
    while s<END:
        e=min(s+15*86400,END); fr+=get(B+'/margin/funding_rates/historical',{'ticker':t,'start_ts':s,'end_ts':e}).get('funding_rates',[]); s=e
    d={x['funding_time']:x for x in fr}; json.dump([d[k] for k in sorted(d)],open(f'data/{t}_funding.json','w')); print(t,'funding',len(d))
# coinbase hourly spot
for p in ['BTC-USD','XRP-USD']:
    out={};s=calendar.timegm((2026,6,3,0,0,0))
    while s<END:
        e=min(s+300*3600,END)
        for r in get(f'https://api.exchange.coinbase.com/products/{p}/candles',{'granularity':3600,'start':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime(s)),'end':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime(e))}): out[r[0]]=r
        s=e; time.sleep(.4)
    json.dump([out[k] for k in sorted(out)],open(f'data/{p}_1h.json','w')); print(p,len(out))
