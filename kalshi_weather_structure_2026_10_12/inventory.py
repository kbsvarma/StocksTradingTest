"""Finite public GET inventory; deliberately yield the first35s/minute."""
import json,time,urllib.request,urllib.parse
from pathlib import Path
P=Path(__file__).resolve().parent
series=json.loads((P/'series_inventory.json').read_text())['response']['series']
out=[]
for s in series:
    if 'temperature' not in s.get('title','').lower() or s.get('frequency')!='hourly':continue
    record={k:s.get(k) for k in ('ticker','title','fee_multiplier','settlement_sources','contract_terms_url')}
    for kind,params in [('open',{'status':'open'}),('oct11',{'min_close_ts':1791691201,'max_close_ts':1791777600})]:
        dest=P/f'inventory_{s["ticker"]}_{kind}.json'
        if dest.exists():body=json.loads(dest.read_text())['response']
        else:
            sec=time.time()%60
            if sec<36:time.sleep(36-sec)
            elif sec>55:time.sleep(96-sec)
            q=urllib.parse.urlencode(dict(series_ticker=s['ticker'],limit=1000,**params))
            url='https://external-api.kalshi.com/trade-api/v2/markets?'+q
            start=time.time()
            with urllib.request.urlopen(url,timeout=15) as r:body=json.load(r)
            dest.write_text(json.dumps({'url':url,'started':start,'received':time.time(),'response':body}))
            time.sleep(1.6)
        assert not body.get('cursor'),'Need pagination; do not report truncated counts'
        ms=body['markets'];events={}
        for m in ms:
            e=events.setdefault(m['event_ticker'],{'contracts':0,'volume':0.,'close':m['close_time']})
            e['contracts']+=1;e['volume']+=float(m.get('volume_fp') or m.get('volume') or 0)
        record[kind]={'markets':len(ms),'events':len(events),'events_with_volume':sum(e['volume']>0 for e in events.values()),'total_contract_volume':sum(e['volume'] for e in events.values()),'details':events}
    out.append(record)
    (P/'hourly_inventory_summary.json').write_text(json.dumps(out,indent=2))
    print(s['ticker'],record['open']['events'],record['oct11']['events'],record['oct11']['total_contract_volume'],flush=True)
