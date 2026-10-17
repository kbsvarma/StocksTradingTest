"""Bounded read-only production feed compatibility probe; no order client writes."""
import sys,json,time
from pathlib import Path
P=Path(__file__).resolve().parent
sys.path.insert(0,str(P/'candidate'))
from book_feed import make_feed
from kalshi_client import Credentials
import market_data
feed=make_feed(Credentials.load())
try:
    for asset in market_data.ASSETS:
        now=time.time()
        rows=market_data.get(market_data.BASE+'/markets?series_ticker=KX'+asset+'15M&status=open&limit=10')['markets']
        m=next(r for r in rows if market_data.timestamp(r['open_time'])<=now<market_data.timestamp(r['close_time']))
        feed.watch(m['ticker'],market_data.timestamp(m['close_time']))
    observations=[]
    for _ in range(30):
        time.sleep(1);observations.append(feed.status())
    result=dict(ts=time.time(),duration=30,observations=observations,
                fresh_seen=sorted({t for r in observations for t in r['fresh_markets']}))
    (P/'feed_probe.json').write_text(json.dumps(result,indent=2))
    print(json.dumps(dict(ts=result['ts'],fresh_seen=result['fresh_seen'],last=observations[-1])))
    assert len(result['fresh_seen'])==4
    assert observations[-1]['connected'] and not observations[-1]['error']
finally:feed.close()
