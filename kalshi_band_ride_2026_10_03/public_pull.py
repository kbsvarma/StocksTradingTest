"""Public GET-only resumable research pull. No order code or credentials."""
import concurrent.futures as cf
import datetime as dt
import json
from pathlib import Path
import sys
import time
import urllib.request
import urllib.parse

ROOT = Path(__file__).resolve().parent / 'fresh_data'
ROOT.mkdir(exist_ok=True)
BASE = 'https://api.elections.kalshi.com/trade-api/v2'
START = int(dt.datetime(2026,9,12,0,tzinfo=dt.timezone.utc).timestamp())
END = int(dt.datetime(2026,9,23,4,tzinfo=dt.timezone.utc).timestamp())
ASSETS = ['BTC','ETH','SOL','XRP']

def get(url):
    for i in range(6):
        try:
            rq=urllib.request.Request(url,headers={'User-Agent':'kalshi-revalidation/1.0'})
            with urllib.request.urlopen(rq,timeout=30) as f: return json.load(f)
        except Exception:
            if i==5: raise
            time.sleep(min(2**i,15))

def ts(s): return int(dt.datetime.fromisoformat(s.replace('Z','+00:00')).timestamp())

def save(path, rows):
    tmp=path.with_suffix('.tmp')
    tmp.write_text(''.join(json.dumps(r,separators=(',',':'))+'\n' for r in rows))
    tmp.replace(path)

def kalshi(asset):
    series='KX'+asset+'15M'; path=ROOT/f'markets_{asset}.jsonl'
    if path.exists(): markets=[json.loads(l) for l in path.read_text().splitlines()]
    else:
        markets=[]; cursor=''
        while True:
            q=dict(series_ticker=series,status='settled',min_close_ts=START,max_close_ts=END,limit=1000)
            if cursor:q['cursor']=cursor
            d=get(BASE+'/markets?'+urllib.parse.urlencode(q))
            markets.extend(m for m in d['markets'] if START<=ts(m['close_time'])<END and m.get('result') in ('yes','no'))
            cursor=d.get('cursor')
            if not cursor:break
            time.sleep(.15)
        markets.sort(key=lambda m:m['close_time']);save(path,markets)
    path=ROOT/f'candles_{asset}.jsonl'
    done={json.loads(l)['ticker'] for l in path.read_text().splitlines()} if path.exists() else set()
    todo=[m for m in markets if m['ticker'] not in done]
    print(asset,'markets',len(markets),'remaining',len(todo),flush=True)
    groups=[]; group=[]
    for m in todo:
        trial=group+[m]
        span=(ts(trial[-1]['close_time'])-ts(trial[0]['open_time']))//60
        if group and (len(trial)>24 or len(trial)*span>9000):
            groups.append(group);group=[]
        group.append(m)
    if group:groups.append(group)
    with path.open('a') as out:
        completed=0
        for group in groups:
            start=ts(group[0]['open_time']); end=ts(group[-1]['close_time'])
            q=dict(market_tickers=','.join(m['ticker'] for m in group),start_ts=start,end_ts=end,period_interval=1)
            d=get(BASE+'/markets/candlesticks?'+urllib.parse.urlencode(q))
            got={r['market_ticker']:r['candlesticks'] for r in d.get('markets',[])}
            for m in group:
                cs=got.get(m['ticker'],[])
                if not cs:raise RuntimeError('empty candles '+m['ticker'])
                compact=[]
                for c in cs:
                    r={'t':c['end_period_ts'],'v':c.get('volume_fp')}
                    for side,prefix in [('yes_bid','b'),('yes_ask','a')]:
                        for field,suffix in [('open','o'),('close','c'),('high','h'),('low','l')]:
                            r[prefix+suffix]=c.get(side,{}).get(field+'_dollars')
                    compact.append(r)
                out.write(json.dumps(dict(ticker=m['ticker'],candles=compact),separators=(',',':'))+'\n')
            out.flush()
            completed+=len(group)
            if completed%480<len(group):print(asset,'candles',completed,'/',len(todo),flush=True)
            time.sleep(.2)

def coinbase(asset):
    path=ROOT/f'spot_{asset}.jsonl'
    done={json.loads(l)['start'] for l in path.read_text().splitlines()} if path.exists() else set()
    with path.open('a') as out:
        for start in range(START-7200,END,290*60):
            if start in done:continue
            q=urllib.parse.urlencode(dict(granularity=60,start=start,end=min(start+290*60,END)))
            rows=get(f'https://api.exchange.coinbase.com/products/{asset}-USD/candles?'+q)
            if not isinstance(rows,list) or not rows:raise RuntimeError('empty spot '+asset)
            out.write(json.dumps(dict(start=start,rows=rows),separators=(',',':'))+'\n');out.flush()
            time.sleep(.25)
    print('spot',asset,'done',flush=True)

if __name__=='__main__':
    mode=sys.argv[1] if len(sys.argv)>1 else 'kalshi'
    if mode=='kalshi': (ROOT/'cutoff.json').write_text(json.dumps(get(BASE+'/historical/cutoff')))
    with cf.ThreadPoolExecutor(max_workers=2) as pool:
        futures=[pool.submit(coinbase if mode=='coinbase' else kalshi,a) for a in ASSETS]
        for f in cf.as_completed(futures):f.result()
