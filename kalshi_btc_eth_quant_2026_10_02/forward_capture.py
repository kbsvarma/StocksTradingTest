"""Bounded read-only BTC/ETH market-data capture. No order/account transport.

Only public REST GETs and fixed market-data WebSocket subscriptions are sent.
Each reconnect starts with fresh snapshots, and sequence gaps remain in the
record. Raw source/receive timestamps permit causal latency replay later.
"""
from pathlib import Path
import argparse, asyncio, collections, datetime as dt, gzip, json, os, signal, sys, time, uuid
import requests
import websockets

P=Path(__file__).resolve().parent
STATE=P/'forward_state'
DATA=STATE/'data'
LIVE_STATUS=Path('/home/varma/kalshi_revalidation_2026_09_12/live_v1/state/status.json')
REST='https://external-api.kalshi.com/trade-api/v2'
WS='wss://external-api-ws.kalshi.com/trade-api/ws/v2'
WS_PATH='/trade-api/ws/v2'
STOP=False

def timestamp(s):return dt.datetime.fromisoformat(s.replace('Z','+00:00')).timestamp()

def subscriptions(tickers):
    if len(tickers)!=2 or {t.split('-')[0] for t in tickers}!={'KXBTC15M','KXETH15M'}:
        raise ValueError('Only current BTC/ETH 15-minute market data is supported')
    return [
        {'id':1,'cmd':'subscribe','params':{'channels':['cfbenchmarks_value','cfbenchmarks_value_5hz'],'index_ids':['BRTI','ETHUSD_RTI']}},
        {'id':2,'cmd':'subscribe','params':{'channels':['orderbook_delta'],'market_tickers':tickers}},
        {'id':3,'cmd':'subscribe','params':{'channels':['trade'],'market_tickers':tickers}},
    ]

def discover():
    markets=[];now=time.time()
    for asset in ('BTC','ETH'):
        response=requests.get(REST+'/markets',params={'series_ticker':'KX'+asset+'15M','status':'open','limit':20},timeout=10)
        response.raise_for_status()
        active=[m for m in response.json().get('markets',[]) if timestamp(m['open_time'])<=now<timestamp(m['close_time'])]
        if not active:return []
        m=min(active,key=lambda m:m['close_time'])
        markets.append({k:m.get(k) for k in ['ticker','open_time','close_time','floor_strike','price_ranges','exchange_index','rules_primary','rules_secondary']})
    return markets if markets[0]['close_time']==markets[1]['close_time'] else []

def auth(credentials,sign):
    ms=str(time.time_ns()//1_000_000)
    return {'KALSHI-ACCESS-KEY':credentials.key_id,'KALSHI-ACCESS-TIMESTAMP':ms,
        'KALSHI-ACCESS-SIGNATURE':sign(credentials.private_key,ms+'GET'+WS_PATH)}

def atomic(path,obj):
    temp=path.with_suffix('.tmp');temp.write_text(json.dumps(obj,indent=2));temp.replace(path)

def write_record(handle,row):
    handle.write(json.dumps({'client_received_at_ms':time.time_ns()//1_000_000,'client_monotonic_ns':time.monotonic_ns(),**row},separators=(',',':'))+'\n')

def gap(previous,message):
    sid,seq=message.get('sid'),message.get('seq')
    if sid is None or seq is None:return None
    prior=previous.get(sid);previous[sid]=seq
    return {'sid':sid,'previous':prior,'current':seq} if prior is not None and seq!=prior+1 else None

def decision_snapshot(path=LIVE_STATUS):
    """Read published decisions only, excluding account, balances and ledger."""
    try:
        source=json.loads(path.read_text())
        return {'type':'live_decision_snapshot','source_ts':source['ts'],
            'mode':source.get('mode'),'scans':source.get('scans',[])}
    except (OSError,ValueError,KeyError):return {'type':'live_decision_snapshot_unavailable'}

async def run(max_windows,max_gib,max_seconds):
    global STOP
    # Load signing only; no live client or order functions are constructed.
    sys.path.insert(0,'/home/varma/kalshi_revalidation_2026_09_12/live_v1')
    from kalshi_client import Credentials,_sign
    credentials=Credentials.load()
    STATE.mkdir(exist_ok=True);DATA.mkdir(exist_ok=True)
    old=json.loads((STATE/'status.json').read_text()) if (STATE/'status.json').exists() else {}
    completed=set(old.get('completed_closes',[]));started=old.get('started_ts',time.time())
    status=dict(mode='READ_ONLY_CAPTURE',real_orders_enabled=False,started_ts=started,completed_closes=sorted(completed),
        max_windows=max_windows,max_gib=max_gib,expires_ts=started+max_seconds,errors=old.get('errors',0),sequence_gaps=old.get('sequence_gaps',0))
    def update(**kwargs):
        status.update(ts=time.time(),**kwargs);atomic(STATE/'status.json',status)
    def quota():return sum(p.stat().st_size for p in DATA.iterdir() if p.is_file())
    update(state='starting')
    while not STOP and len(completed)<max_windows and time.time()<started+max_seconds:
        if quota()>=max_gib*1024**3:update(state='data_quota_reached');return
        handle=None;part=None;counts=collections.Counter();by_market=collections.Counter();by_index=collections.Counter();previous={}
        try:
            markets=await asyncio.to_thread(discover)
            if not markets:await asyncio.sleep(3);continue
            close=min(timestamp(m['close_time']) for m in markets)
            if close in completed:await asyncio.sleep(2);continue
            tickers=[m['ticker'] for m in markets]
            part=DATA/f'capture_{int(close)}_{uuid.uuid4().hex[:8]}.jsonl.gz.part'
            handle=gzip.open(part,'wt',compresslevel=3)
            write_record(handle,{'type':'capture_start','markets':markets,'partial_start':time.time()>timestamp(markets[0]['open_time'])+5})
            update(state='connecting',tickers=tickers,connection_started_ts=time.time(),last_message_ts=time.time(),counts={},by_market={},by_index={})
            async with websockets.connect(WS,additional_headers=auth(credentials,_sign),ping_interval=20,ping_timeout=20,max_queue=2000) as ws:
                for sub in subscriptions(tickers):await ws.send(json.dumps(sub))
                last_status=0
                while not STOP and time.time()<min(close+2,started+max_seconds):
                    try:raw=await asyncio.wait_for(ws.recv(),timeout=2)
                    except asyncio.TimeoutError:
                        if time.time()-status.get('last_message_ts',time.time())>15:raise TimeoutError('No market data for 15 seconds')
                        continue
                    message=json.loads(raw);kind=message.get('type','unknown');counts[kind]+=1
                    msg=message.get('msg',{})
                    if msg.get('market_ticker'):by_market[msg['market_ticker']]+=1
                    if msg.get('index_id'):by_index[msg['index_id']]+=1
                    missed=gap(previous,message)
                    if missed:
                        status['sequence_gaps']+=1;write_record(handle,{'type':'capture_sequence_gap',**missed})
                    write_record(handle,message);status['last_message_ts']=time.time()
                    if kind=='error':raise ValueError('Market-data subscription rejected; see captured error frame')
                    if time.time()-last_status>=5:
                        write_record(handle,decision_snapshot())
                        handle.flush();size=quota()
                        update(state='streaming',counts=dict(counts),by_market=dict(by_market),by_index=dict(by_index),bytes=size)
                        last_status=time.time()
                        if size>=max_gib*1024**3:STOP=True;status['stop_reason']='data_quota';break
            if not STOP and time.time()>=close:
                completed.add(close);status['completed_closes']=sorted(completed)
            write_record(handle,{'type':'capture_end','counts':dict(counts),'complete_to_close':close in completed})
        except Exception as exc:
            status['errors']+=1
            if handle:write_record(handle,{'type':'capture_error','error_class':type(exc).__name__})
            update(state='reconnecting',error_class=type(exc).__name__)
            await asyncio.sleep(5)
        finally:
            if handle:handle.close()
            if part and part.exists():part.rename(part.with_suffix(''))
    update(state='stopped' if STOP else 'completed',completed_closes=sorted(completed),bytes=quota())

def stop(*_):
    global STOP
    STOP=True

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--max-windows',type=int,default=500);p.add_argument('--max-gib',type=float,default=2);p.add_argument('--max-seconds',type=int,default=7*86400);a=p.parse_args()
    signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
    asyncio.run(run(a.max_windows,a.max_gib,a.max_seconds))
