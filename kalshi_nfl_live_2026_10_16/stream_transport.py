"""Read-only WebSocket book/fill transport. No order client or ledger access.

Only orderbook_delta and fill can be subscribed. Uses explicit YES-coordinate
prices and converts NO prices back to the existing REST representation.
"""
from collections import Counter, OrderedDict, deque
from decimal import Decimal as D
import json
import os
from pathlib import Path
import queue
import threading
import time

WS_URL='wss://external-api-ws.kalshi.com/trade-api/ws/v2'
CHANNELS=frozenset(('orderbook_delta','fill'))
ASSETS=('BTC','ETH','SOL','XRP','NATGAS','EURUSD','WTI','USDCAD')
SOURCE_LAG_LIMIT=1.0
LIVENESS_LIMIT=2.0
SNAPSHOT_LIMIT=25.0
SNAPSHOT_REFRESH_SECONDS=10.0

def allowed(ticker):
    return isinstance(ticker,str) and ticker.startswith('KXNFLGAME-')

def decimal(value):
    n=D(str(value))
    if not n.is_finite():raise ValueError('Nonfinite stream value')
    return n

def subscription(ident,channel,tickers=None):
    if channel not in CHANNELS:raise ValueError('Unapproved WebSocket channel')
    p={'channels':[channel]}
    if tickers is not None:p['market_tickers']=list(tickers)
    if channel=='orderbook_delta':p['use_yes_price']=True
    return dict(id=ident,cmd='subscribe',params=p)

class BookState:
    def __init__(self):self.snapshot_checks=Counter();self.snapshot_refreshes=deque(maxlen=10);self.reset()
    def reset(self):
        self.books={};self.seq={};self.meta={}
    def apply(self,frame,wall,mono):
        try:return self._apply(frame,wall,mono)
        except Exception:
            self.reset();raise
    def _apply(self,frame,wall,mono):
        kind=frame.get('type')
        if kind=='ok' and frame.get('sid') in self.seq:
            sid=frame['sid'];seq=frame['seq']
            if type(seq) is not int or seq!=self.seq[sid]+1:raise ValueError('Control sequence gap')
            self.seq[sid]=seq
            return
        if kind not in ('orderbook_snapshot','orderbook_delta'):return
        sid,seq=frame['sid'],frame['seq'];m=frame['msg'];ticker=m['market_ticker']
        if not allowed(ticker):raise ValueError('Unexpected market')
        if type(seq) is not int or (sid in self.seq and seq!=self.seq[sid]+1):
            raise ValueError('Book sequence gap or duplicate')
        sent=frame.get('sending_ts_ms')
        if not isinstance(sent,(int,float)) or not -.25<=wall-sent/1000<=SOURCE_LAG_LIMIT:
            raise ValueError('Stale/future/missing book timestamp')
        if kind=='orderbook_snapshot':
            book={}
            for side in ('yes','no'):
                levels={}
                for px,qty in m.get(side+'_dollars_fp',[]):
                    px,qty=decimal(px),decimal(qty)
                    if not 0<=px<=1 or qty<0:raise ValueError('Invalid book level')
                    px=1-px if side=='no' else px
                    if px in levels:raise ValueError('Duplicate snapshot level')
                    if qty:levels[px]=qty
                book[side]=levels
            # A full snapshot is authoritative new state, not an assertion that
            # every preceding change was delivered as a separate delta.
            if ticker in self.books:
                same=self.books[ticker]==book
                self.snapshot_checks['unchanged' if same else 'refreshed']+=1
                if not same:
                    diff=[dict(side=side,price=str(px),old=str(self.books[ticker][side].get(px,0)),new=str(book[side].get(px,0)))
                          for side in ('yes','no') for px in set(self.books[ticker][side])|set(book[side])
                          if self.books[ticker][side].get(px,0)!=book[side].get(px,0)]
                    self.snapshot_refreshes.append(dict(ticker=ticker,ts=wall,seq=seq,differences=diff[:20]))
            self.books[ticker]=book
            self.meta[ticker]={'snapshot_mono':mono}
        else:
            if ticker not in self.books:raise ValueError('Delta before snapshot')
            side=m['side'];px=decimal(m['price_dollars']);delta=decimal(m['delta_fp'])
            if side not in ('yes','no') or not 0<=px<=1:raise ValueError('Invalid delta')
            px=1-px if side=='no' else px
            levels=self.books[ticker][side];qty=levels.get(px,D(0))+delta
            if qty<0:raise ValueError('Negative book depth')
            if qty:levels[px]=qty
            else:levels.pop(px,None)
        self.seq[sid]=seq
        self.meta[ticker].update(seq=seq,sid=sid,received_ts=wall,received_mono=mono,
                                 source_ts=sent/1000,source_lag_ms=1000*wall-sent)
        return ticker
    def view(self,ticker,mono):
        if ticker not in self.books:return None
        meta=self.meta[ticker]
        if not 0<=mono-meta['snapshot_mono']<=SNAPSHOT_LIMIT:return None
        book=self.books[ticker]
        # Reject transient crossed states instead of treating them as cheap depth.
        if book['yes'] and book['no'] and max(book['yes'])+max(book['no'])>1:return None
        out={side+'_dollars':[[str(p),str(q)] for p,q in sorted(
             sorted(book[side].items(),reverse=True)[:10])] for side in ('yes','no')}
        out['_transport']=dict(source='websocket',seq=meta['seq'],sid=meta['sid'],
             received_ts=meta['received_ts'],source_ts=meta['source_ts'],
             source_lag_ms=meta['source_lag_ms'],snapshot_age_ms=1000*(mono-meta['snapshot_mono']))
        return out

class FillState:
    def __init__(self):self.fills=OrderedDict();self.duplicates=0;self.last_reason='not_checked'
    def apply(self,msg,wall):
        if not allowed(msg.get('market_ticker')):return False
        if msg.get('subaccount',0)!=0:return False
        ident=(msg['exchange_index'],msg.get('subaccount',0),msg['trade_id'])
        if ident in self.fills:
            if self.fills[ident]['raw']!=msg:raise ValueError('Conflicting duplicate fill')
            self.duplicates+=1;return False
        n=decimal(msg['count_fp']);p=decimal(msg['yes_price_dollars']);fee=decimal(msg['fee_cost'])
        if n<=0 or not 0<=p<=1 or fee<0:raise ValueError('Invalid fill economics')
        row={'raw':dict(msg),'received_ts':wall}
        self.fills[ident]=row
        while len(self.fills)>10000:self.fills.popitem(last=False)
        return True
    def confirmed(self,order,record):
        # Absence of a streamed fill NEVER establishes a zero-fill cancellation.
        def reject(reason):self.last_reason=reason;return None
        self.last_reason='checking'
        if order.get('status') not in ('executed','canceled'):return reject('order_not_terminal')
        if order.get('order_id')!=record.get('order_id') or order.get('ticker')!=record['ticker']:
            return reject('order_identity_mismatch')
        if order.get('client_order_id')!=record['client_id']:return reject('client_identity_mismatch')
        side=record['side'];book_side={'yes':'bid','no':'ask'}.get(side)
        # Events orders use canonical outcome/book sides. NO entries can be
        # represented as legacy YES sells; legacy action is not entry direction.
        if (book_side is None or order.get('outcome_side')!=side or order.get('book_side')!=book_side):
            return reject('order_direction_mismatch')
        if (order.get('exchange_index')!=record['exchange_index'] or
                order.get('subaccount_number',order.get('subaccount',0))!=0):
            return reject('order_route_mismatch')
        try:
            expected=decimal(order['fill_count_fp'])
            if expected<=0 or decimal(order['remaining_count_fp'])!=0:return reject('order_zero_or_remaining')
            selected=[x for x in self.fills.values() if x['raw']['order_id']==record['order_id']]
            count=cost=fees=D(0);out=[]
            for x in selected:
                f=x['raw']
                if (f['market_ticker']!=record['ticker'] or f['exchange_index']!=record['exchange_index']
                    or f.get('subaccount',0)!=0):return reject('fill_identity_or_route_mismatch')
                if (f.get('outcome_side')!=side or f.get('book_side')!=book_side or
                    f.get('purchased_side',side)!=side):return reject('fill_direction_mismatch')
                if f.get('client_order_id',record['client_id'])!=record['client_id']:
                    return reject('fill_client_mismatch')
                # Legacy buy/sell are compatible only when both canonical sides
                # match this exact order. An explicit close remains unsupported.
                if f.get('action') not in ('buy','sell',''):
                    return reject('unsupported_legacy_action')
                if (f.get('action') and order.get('action') and f['action']!=order['action']):
                    return reject('legacy_action_mismatch')
                n=decimal(f['count_fp']);p=decimal(f['yes_price_dollars']);fee=decimal(f['fee_cost'])
                count+=n;cost+=n*(p if record['side']=='yes' else 1-p);fees+=fee
                out.append(dict(fill_id=f['trade_id'],order_id=f['order_id'],ticker=f['market_ticker'],
                     count_fp=str(n),yes_price_dollars=str(p),fee_cost=str(fee),
                     ws_received_ts=x['received_ts'],ws_event_ts=f.get('ts_ms',0)/1000))
            if count!=expected:return reject('incomplete_or_excess_quantity')
            if cost!=sum(decimal(order[k]) for k in ('maker_fill_cost_dollars','taker_fill_cost_dollars')):return reject('cost_mismatch')
            if fees!=sum(decimal(order[k]) for k in ('maker_fees_dollars','taker_fees_dollars')):return reject('fee_mismatch')
            self.last_reason='verified'
            return out
        except (KeyError,ValueError,TypeError,ArithmeticError):return reject('invalid_or_missing_economics')

class FillAudit:
    """Bounded asynchronous receipt writer; no disk I/O on the execution path."""
    def __init__(self,path):
        self.path=Path(path);self.queue=queue.Queue(maxsize=1024)
        self.stop=threading.Event();self.written=0;self.dropped=0;self.errors=0
        self.thread=threading.Thread(target=self._run,name='kalshi-fill-receipts',daemon=True)
        self.thread.start()
    def append(self,row):
        try:self.queue.put_nowait(row)
        except queue.Full:self.dropped+=1
    def _run(self):
        while not self.stop.is_set() or not self.queue.empty():
            try:row=self.queue.get(timeout=.1)
            except queue.Empty:continue
            try:
                with self.path.open('a') as f:
                    f.write(json.dumps(row)+'\n');f.flush();os.fsync(f.fileno())
                self.written+=1
            except (OSError,TypeError,ValueError):self.errors+=1
            finally:self.queue.task_done()
    def close(self):self.stop.set();self.thread.join(timeout=4)

class StreamTransport:
    def __init__(self,headers,connector=None,on_frame=None,start=True,audit_path=None):
        self.headers=headers;self.connector=connector;self.on_frame=on_frame
        self.lock=threading.RLock();self.stop=threading.Event();self.wake=threading.Event()
        self.books=BookState();self.fills=FillState();self.targets={};self.connected=False
        self.channels=set();self.last_alive=0.;self.error='';self.generation=0
        self.errors=deque(maxlen=20);self.stats=Counter();self.lags=deque(maxlen=20000);self.pending_notice=False
        self.fill_reasons=Counter();self.audit=FillAudit(audit_path) if audit_path is not None else None
        self.thread=None;self.socket=None
        if start:
            self.thread=threading.Thread(target=self._run,name='kalshi-ws-read-only',daemon=True)
            self.thread.start()
    def watch(self,ticker,close):
        if not allowed(ticker) or not isinstance(close,(int,float)) or close<=time.time():return
        with self.lock:
            self.targets={t:c for t,c in self.targets.items() if c>time.time()}
            self.targets[ticker]=close
    def book(self,ticker):
        with self.lock:
            now=time.monotonic()
            if ticker not in self.targets or self.targets[ticker]<=time.time():
                self.stats['book_fallback']+=1;return None
            if not self.connected or 'orderbook_delta' not in self.channels or not 0<=now-self.last_alive<=LIVENESS_LIMIT:
                self.stats['book_fallback']+=1;return None
            out=self.books.view(ticker,now)
            self.stats['book_hit' if out is not None else 'book_fallback']+=1
            if out is not None:out['_transport']['generation']=self.generation
            return out
    def confirmed_fills(self,order,record):
        with self.lock:
            out=self.fills.confirmed(order,record)
            self.stats['fills_hit' if out is not None else 'fills_fallback']+=1
            self.fill_reasons[self.fills.last_reason]+=1
            if self.audit:
                self.audit.append(dict(kind='fill_verification',ts=time.time(),
                    ticker=record['ticker'],order_id=record.get('order_id'),
                    client_id=record['client_id'],side=record['side'],exchange_index=record['exchange_index'],
                    reason=self.fills.last_reason,transport='websocket' if out is not None else 'rest',
                    terminal_order={k:v for k,v in order.items() if k!='user_id'},receipts=out))
            return out
    def consume_notice(self):
        with self.lock:
            value=self.pending_notice;self.pending_notice=False
            return value
    def wait(self,seconds):
        self.wake.wait(seconds);self.wake.clear()
    def status(self):
        with self.lock:
            now=time.monotonic()
            return dict(version=1,mode='WS_PRIMARY_REST_FALLBACK',connected=self.connected,
                        channels=sorted(self.channels),error=self.error,generation=self.generation,
                        alive_age_ms=max(0,1000*(now-self.last_alive)) if self.last_alive else None,
                        targets=sorted(self.targets),valid_books=[t for t in self.targets if
                        self.connected and now-self.last_alive<=LIVENESS_LIMIT and self.books.view(t,now) is not None],
                        stats=dict(self.stats),fill_duplicates=self.fills.duplicates,
                        recent_errors=list(self.errors),snapshot_checks=dict(self.books.snapshot_checks),
                        recent_snapshot_refreshes=list(self.books.snapshot_refreshes),paid_channels=False,
                        fill_validation_version=2,fill_verification_reasons=dict(self.fill_reasons),
                        fill_audit=None if self.audit is None else dict(written=self.audit.written,
                            dropped=self.audit.dropped,errors=self.audit.errors,pending=self.audit.queue.qsize()))
    def close(self):
        self.stop.set();self.wake.set()
        ws=self.socket
        if ws is not None:
            try:ws.close()
            except Exception:pass
        if self.thread:self.thread.join(timeout=4)
        if self.audit:self.audit.close()
    def invalidate(self,reason):
        with self.lock:
            self.connected=False;self.channels.clear();self.books.reset()
            self.error=reason;self.errors.append(dict(ts=time.time(),reason=reason));self.stats['disconnects']+=1
    def _frame(self,r):
        wall=time.time();mono=time.monotonic()
        if self.on_frame:self.on_frame(r,wall)
        with self.lock:
            self.last_alive=mono;self.stats['frames']+=1
            kind=r.get('type')
            if kind=='error':raise ValueError('WebSocket subscription error '+str(r.get('msg',{}))[:160])
            if kind=='subscribed':
                channel=r['msg']['channel']
                if channel not in CHANNELS:raise ValueError('Unapproved channel response')
                self.channels.add(channel)
            elif kind in ('orderbook_snapshot','orderbook_delta'):
                ticker=self.books.apply(r,wall,mono)
                # In-flight frames for an unsubscribed/expired ticker still advance
                # the channel sequence, but cannot be returned by book().
                self.stats[kind]+=1
                self.lags.append(1000*wall-r['sending_ts_ms'])
            elif kind=='ok':self.books.apply(r,wall,mono)
            elif kind=='fill':
                if self.audit:self.audit.append(dict(kind='fill_frame',ts=wall,
                    frame={**r,'msg':{k:v for k,v in r['msg'].items() if k!='user_id'}}))
                if self.fills.apply(r['msg'],wall):
                    self.stats['fill_notifications']+=1;self.pending_notice=True;self.wake.set()
    def _run(self):
        delay=.5
        while not self.stop.is_set():
            try:
                if self.connector is None:
                    from websockets.sync.client import connect
                    connector=connect
                else:connector=self.connector
                with connector(WS_URL,additional_headers=self.headers(),open_timeout=3,close_timeout=1,
                               ping_interval=None,max_queue=32,max_size=2**22) as ws:
                    self.socket=ws
                    with self.lock:
                        self.books.reset();self.channels.clear();self.connected=True
                        self.last_alive=time.monotonic();self.generation+=1;self.error=''
                    ident=1;ws.send(json.dumps(subscription(ident,'fill')))
                    subscribed=set();book_sid=None;last_snapshot=0.;last_ping=0.;pong=None;ping_at=0.
                    while not self.stop.is_set():
                        mono=time.monotonic()
                        with self.lock:
                            self.targets={t:c for t,c in self.targets.items() if c>time.time()}
                            targets=set(self.targets)
                            for t,m in list(self.books.meta.items()):
                                if t not in targets and mono-m['received_mono']>120:
                                    self.books.books.pop(t,None);self.books.meta.pop(t,None)
                        if pong is not None:
                            if pong.is_set():
                                with self.lock:self.last_alive=mono
                                pong=None
                            elif mono-ping_at>1.5:raise TimeoutError('WebSocket ping overdue')
                        if pong is None and mono-last_ping>=1:
                            pong=ws.ping();last_ping=mono;ping_at=mono
                        if book_sid is None and targets and not subscribed:
                            ident+=1;ws.send(json.dumps(subscription(ident,'orderbook_delta',sorted(targets))))
                            subscribed=targets;last_snapshot=mono
                        elif book_sid is not None:
                            for action,ts in [('add_markets',targets-subscribed),('delete_markets',subscribed-targets)]:
                                if ts:
                                    ident+=1;ws.send(json.dumps(dict(id=ident,cmd='update_subscription',
                                        params=dict(sids=[book_sid],market_tickers=sorted(ts),action=action))))
                            subscribed=targets
                            if targets and mono-last_snapshot>=SNAPSHOT_REFRESH_SECONDS:
                                ident+=1;ws.send(json.dumps(dict(id=ident,cmd='update_subscription',
                                    params=dict(sids=[book_sid],market_tickers=sorted(targets),action='get_snapshot'))))
                                last_snapshot=mono
                        try:raw=ws.recv(timeout=.1)
                        except TimeoutError:continue
                        r=json.loads(raw)
                        if r.get('type')=='subscribed' and r.get('msg',{}).get('channel')=='orderbook_delta':
                            book_sid=r['msg']['sid']
                        self._frame(r);delay=.5
            except Exception as exc:
                self.invalidate(type(exc).__name__+': '+str(exc)[:180])
                self.stop.wait(delay);delay=min(10,delay*2)
            finally:
                self.socket=None
                with self.lock:self.connected=False;self.channels.clear();self.books.reset()

def make_stream(credentials,**kwargs):
    from kalshi_client import _sign
    def headers():
        ts=str(time.time_ns()//1_000_000)
        return {'KALSHI-ACCESS-KEY':credentials.key_id,'KALSHI-ACCESS-TIMESTAMP':ts,
                'KALSHI-ACCESS-SIGNATURE':_sign(credentials.private_key,ts+'GET/trade-api/ws/v2')}
    return StreamTransport(headers,**kwargs)
