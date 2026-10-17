"""Public GET-only climate catalog, rain books and precipitation archive."""
import csv, io, json, re, time
from collections import Counter
from pathlib import Path
import requests

P=Path(__file__).resolve().parent
ROOT=P.parent
BASE='https://external-api.kalshi.com/trade-api/v2'
S=requests.Session()
def save(name,obj): (P/name).write_text(json.dumps(obj,indent=2))
def get(url,params=None):
    started=time.time()
    for attempt in range(4):
        r=S.get(url,params=params,timeout=45)
        if r.status_code!=429:break
        print('PUBLIC_RATE_LIMIT',url,'backoff',5*(attempt+1),flush=True)
        time.sleep(5*(attempt+1))
    r.raise_for_status()
    return dict(url=r.url,started=started,received=time.time(),response=r.json())

def inventory():
    series=json.load(open(P/'series.json'))
    ss=series.get('series',series) if isinstance(series,dict) else series
    names={s['ticker'] for s in ss};cur=None;out=[];total=0
    while True:
        p={'status':'open','limit':200}
        if cur:p['cursor']=cur
        d=get(BASE+'/events',p)['response'];es=d['events'];total+=len(es)
        out.extend(e for e in es if e.get('series_ticker') in names)
        cur=d.get('cursor')
        if total%2000==0:print('CATALOG',total,len(out),flush=True)
        if not cur:break
        time.sleep(1.)
    save('active_events.json',dict(received=time.time(),total_events_scanned=total,events=out))
    print('ACTIVE_CLIMATE',len(out),'series',len({e['series_ticker'] for e in out}),flush=True)

def precip():
    d=get('https://weather.com/kalshi/api/climate/primary',{'date':'2026-10-15'})
    save('station_metadata.json',d)
    mapping={r['station']['cliId']:r['station'] for r in d['response']['results']}
    markets=json.load(open(ROOT/'kalshi_every_window_marketwide_2026_10_10/daily/KXRAIN/markets.json'))
    ids=set();errors=[]
    for m in markets:
        match=re.search(r'at (CLI[A-Z]{3}|K[A-Z]{3}) ',m['rules_primary'])
        if not match:errors.append(m['ticker']);continue
        ident=match.group(1).removeprefix('CLI').removeprefix('K')
        if ident not in mapping:errors.append(m['ticker']);continue
        ids.add(mapping[ident]['icao'][1:])
    params={'station':sorted(ids),'data':'p01i','sts':'2026-08-15T00:00:00Z','ets':'2026-10-11T00:00:00Z','tz':'Etc/UTC','format':'onlycomma','report_type':'3','missing':'M','trace':'empty','latlon':'no'}
    start=time.time();r=S.get('https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py',params=params,timeout=90);r.raise_for_status()
    (P/'precip.csv').write_text(r.text)
    save('precip_receipt.json',dict(url=r.url,started=start,received=time.time(),stations=sorted(ids),unmapped=errors,bytes=len(r.content),note='Archived observations; actual first-arrival and revision history unavailable.'))
    print('PRECIP',len(r.content),'stations',len(ids),'unmapped',len(errors),r.text[:150],flush=True)

def books():
    for ser in ['KXRAIN','KXRAINWKND']:
        d=get(BASE+'/markets',{'series_ticker':ser,'status':'open','limit':1000});save(ser+'_open_receipt.json',d)
    day=json.load(open(P/'KXRAIN_open_receipt.json'))['response']['markets']
    wk=json.load(open(P/'KXRAINWKND_open_receipt.json'))['response']['markets']
    day={m['ticker'].rsplit('-',1)[-1]:m for m in day if m['event_ticker']=='KXRAIN-26OCT17'}
    wk={m['ticker'].rsplit('-',1)[-1]:m for m in wk if m['event_ticker']=='KXRAINWKND-26OCT17'}
    pairs=[];receipts={}
    for c in sorted(day.keys() & wk.keys()):
        a,b=day[c],wk[c]
        station=lambda m:re.search(r'at (\S+) ',m['rules_primary']).group(1)
        if station(a)!=station(b):continue
        if not (a['floor_strike']==b['floor_strike']==0 and a['strike_type']==b['strike_type']=='greater'):continue
        pairs.append(dict(city=c,day=a,weekend=b))
        for m in (a,b):
            receipts[m['ticker']]=get(BASE+'/markets/'+m['ticker']+'/orderbook',{'depth':100})
            time.sleep(.10)
    save('rain_pairs.json',pairs);save('rain_book_receipts.json',receipts)
    print('PAIRS',len(pairs),'books',len(receipts),flush=True)

def lows():
    es=json.load(open(P/'active_events.json'))['events']
    names=sorted({e['series_ticker'] for e in es if e['series_ticker'].startswith('KXLOWT')})
    receipts={};summary=[]
    for name in names:
        d=get(BASE+'/markets',dict(series_ticker=name,status='open',limit=1000));receipts[name]=d
        ms=d['response']['markets'];eligible=[]
        for m in ms:
            ya=float(m.get('yes_ask_dollars') or 1);yb=float(m.get('yes_bid_dollars') or 0)
            if .05<=ya<=.95 and ya-yb<=.05+1e-9:eligible.append(m['ticker'])
        summary.append(dict(series=name,markets=len(ms),volume24h_contracts=sum(float(m.get('volume_24h_fp') or 0) for m in ms),two_sided_within5c=eligible))
        time.sleep(1.)
    save('daily_low_receipts.json',receipts);save('daily_low_liquidity.json',summary)
    print('DAILY_LOW_TOP',sorted(summary,key=lambda r:r['volume24h_contracts'],reverse=True)[:8],flush=True)

if __name__=='__main__':
    import sys
    globals()[sys.argv[1]]()
