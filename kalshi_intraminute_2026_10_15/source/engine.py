"""Five-contract execution state machine. Construct with live=False by default.

Up to three IOCs per market, only after confirmed zero-fill cancellation.
Active assets may requalify on a fresh later minute after confirmed zero fills.
A failed/ambiguous POST is NEVER repeated. SQLite retains every durable intent.
"""
from __future__ import annotations
from datetime import datetime
from decimal import Decimal, ROUND_FLOOR, ROUND_CEILING
from io_budget import get_budget
import json
import sqlite3
import time
import uuid
from pathlib import Path
from zoneinfo import ZoneInfo

D=Decimal
from lanes import ALL_ASSETS, NEW_ASSETS, route_for, record_asset, asset_permission
ASSETS=ALL_ASSETS
SERIES=tuple('KX'+a+'15M' for a in ASSETS)
ET=ZoneInfo('America/New_York')
ACTIVE=('intent','unknown','ack','open','accounting_pending')
from sizing import CONTRACTS, MAX_CONTRACTS, MAX_RISK, DAILY_LOSS_BUDGET, MIN_CASH
from lanes import lane_for, Z3, Z3_MAX_CONTRACTS, Z3_MAX_RISK, Z3_DAILY_BUDGET, z3_permission
from execution_retry import retry_candidate, fresh_retry_candidate, fresh_retry_matches, MAX_IOC_SUBMISSIONS, MIN_IOC_RETRY_INTERVAL_SECONDS, RETRY_DEADLINE_SECONDS
CRYPTO_EXCHANGE_INDEX=2

def number(row,*names):
    for name in names:
        if row.get(name) is not None:
            v=D(str(row[name]))
            if not v.is_finite():raise ValueError('non-finite '+name)
            return v
    raise ValueError('Missing numeric field: '+','.join(names))

def requested_count(record):
    # Records created before this release are one-contract orders.
    n=D(str(record.get('requested_count', '1')))
    if not n.is_finite() or n not in (D('1'), D('2'), D('3'), D('5')):
        raise ValueError('Invalid requested count')
    return n

def crypto(ticker):return any(ticker.startswith(s+'-') for s in SERIES)
def asset(ticker):return ticker.split('-')[0]
def today():return datetime.now(ET).strftime('%Y-%m-%d')

def limit_price(signal):
    """Round toward less spending on the market's YES-perspective tick ladder."""
    rule=lane_for(signal)
    cap=min(D(str(signal['signal_ask']))+D('.01'),rule.cap)
    yes=cap if signal['side']=='yes' else 1-cap
    ranges=signal.get('price_ranges')
    if not ranges:raise ValueError('market tick ladder unavailable')
    for r in ranges:
        lo,hi,step=D(r['start']),D(r['end']),D(r['step'])
        if step<=0:raise ValueError('invalid tick step')
        if lo<=yes<=hi:
            direction=ROUND_FLOOR if signal['side']=='yes' else ROUND_CEILING
            px=lo+((yes-lo)/step).to_integral_value(rounding=direction)*step
            if not lo<=px<=hi:continue
            spend=px if signal['side']=='yes' else 1-px
            if not rule.floor<=spend<=cap:raise ValueError('price cannot fit band and ticks')
            return px,spend
    raise ValueError('no valid price tick')

class Store:
    def __init__(self,path):
        self.path=Path(path);self.path.parent.mkdir(parents=True,exist_ok=True)
        self.db=sqlite3.connect(str(self.path))
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.execute('CREATE TABLE IF NOT EXISTS orders (ticker TEXT PRIMARY KEY, record TEXT NOT NULL)')
        self.db.execute('CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY, ts REAL, event TEXT)')
        self.db.execute('CREATE TABLE IF NOT EXISTS order_attempts (client_id TEXT PRIMARY KEY, ticker TEXT NOT NULL, attempt_number INTEGER NOT NULL, record TEXT NOT NULL, UNIQUE(ticker,attempt_number))')
        # Additive migration: retain the original orders and all historical P&L.
        for (raw,) in self.db.execute('SELECT record FROM orders').fetchall():
            r=json.loads(raw)
            if r.get('client_id'):
                self.db.execute('INSERT OR IGNORE INTO order_attempts VALUES (?,?,?,?)',
                                (r['client_id'],r['ticker'],r.get('attempt_number',1),raw))
        self.db.commit()
    def all(self):return [json.loads(x[0]) for x in self.db.execute('SELECT record FROM orders ORDER BY rowid')]
    def entry_records(self):
        # Ownership needs every ticker, but only canceled-zero records can reopen.
        # Avoid decoding the entire settled history for every asset/lane scan.
        return [(t,json.loads(raw) if raw else None) for t,raw in self.db.execute(
            "SELECT ticker,CASE WHEN json_extract(record,'$.status')='unfilled' THEN record END FROM orders")]
    def pending(self):
        return self.db.execute("SELECT 1 FROM orders WHERE json_extract(record,'$.status') IN ('intent','unknown','ack','accounting_pending') LIMIT 1").fetchone() is not None
    def get(self,ticker):
        row=self.db.execute('SELECT record FROM orders WHERE ticker=?',(ticker,)).fetchone()
        return json.loads(row[0]) if row else None
    def save(self,r):
        current=self.get(r['ticker'])
        if current and current.get('client_id')!=r.get('client_id'):
            raise RuntimeError('Refusing to overwrite a newer market attempt')
        with self.db:
            self.db.execute('INSERT INTO orders VALUES (?,?) ON CONFLICT(ticker) DO UPDATE SET record=excluded.record',(r['ticker'],json.dumps(r)))
            self._save_attempt(r)
    def _save_attempt(self,r):
        if r.get('client_id'):
            self.db.execute('INSERT INTO order_attempts VALUES (?,?,?,?) ON CONFLICT(client_id) DO UPDATE SET record=excluded.record',
                            (r['client_id'],r['ticker'],r.get('attempt_number',1),json.dumps(r)))
    def attempts(self,ticker):
        return [json.loads(x[0]) for x in self.db.execute('SELECT record FROM order_attempts WHERE ticker=? ORDER BY attempt_number',(ticker,))]
    def reserve(self,r,previous=None):
        # Compare-and-swap under a write lock prevents two retry writers winning.
        try:
            self.db.execute('BEGIN IMMEDIATE')
            current=self.get(r['ticker'])
            if previous is None:
                self.db.execute('INSERT INTO orders VALUES (?,?)',(r['ticker'],json.dumps(r)))
            else:
                eligible=(retry_candidate(current,r['signal']['decision_ts'],r['created_ts'],r['lane']) or
                          fresh_retry_matches(current,r['signal'],r['created_ts']))
                if current!=previous or not eligible:
                    raise RuntimeError('Market attempt changed or retry no longer eligible')
                if r['attempt_number']!=current['attempt_number']+1:
                    raise RuntimeError('Nonsequential retry identity')
                self._save_attempt(current)
                self.db.execute('UPDATE orders SET record=? WHERE ticker=?',(json.dumps(r),r['ticker']))
            self._save_attempt(r)
            self.db.commit()
        except BaseException:
            self.db.rollback();raise
    def event(self,r):
        self.db.execute('INSERT INTO events(ts,event) VALUES (?,?)',(time.time(),json.dumps(r)));self.db.commit()

class Exchange:
    def __init__(self,client,account_reader=None,stream=None):
        self.client=client;self.account_reader=account_reader;self.stream=stream
    def account_snapshot(self,index):
        if self.account_reader is not None:return self.account_reader.snapshot(index)
        return self.positions(),self.orders(status='resting'),self.balance(index)
    def pages(self,method,key,**params):
        out=[];cursor='';seen=set()
        while True:
            d=method(**dict(params,limit=1000,**({'cursor':cursor} if cursor else {})))
            if not isinstance(d,dict) or key not in d:raise ValueError('exchange response missing '+key)
            out.extend(d[key]);cursor=d.get('cursor') or ''
            if not cursor:return out
            if cursor in seen:raise ValueError('pagination did not advance')
            seen.add(cursor)
    def positions(self):return self.pages(self.client.get_positions,'market_positions',subaccount=0)
    def orders(self,**params):return self.pages(self.client.get_orders,'orders',subaccount=0,**params)
    def fills(self,oid):return self.pages(self.client.get_fills,'fills',order_id=oid,subaccount=0)
    def order(self,oid):return self.client._request('GET','/portfolio/orders/'+oid)['order']
    def balance(self,exchange_index=CRYPTO_EXCHANGE_INDEX):
        if exchange_index not in (0,2):raise ValueError('Unsupported balance route')
        r=self.client._request('GET','/portfolio/balance',params={'subaccount':0,'exchange_index':exchange_index})
        return number(r,'balance_dollars') if r.get('balance_dollars') is not None else number(r,'balance')/100
    def market(self,ticker):return self.client.get_market(ticker)['market']
    def book(self,ticker):
        if self.stream is not None:
            book=self.stream.book(ticker)
            if book is not None:return book
        return self.client.get_orderbook(ticker,depth=10)['orderbook_fp']
    def fill_receipts(self,order,record):
        if self.stream is not None:
            fills=self.stream.confirmed_fills(order,record)
            if fills is not None:return fills
        return self.fills(record['order_id'])
    def submit(self,r):
        return self.client._request('POST','/portfolio/events/orders',body={
            'ticker':r['ticker'],'client_order_id':r['client_id'],
            'side':'bid' if r['side']=='yes' else 'ask',
            'count':f'{requested_count(r):.2f}','price':r['yes_limit'],'time_in_force':'immediate_or_cancel',
            'self_trade_prevention_type':'taker_at_cross','post_only':False,
            'cancel_order_on_pause':True,'reduce_only':False,'subaccount':0,
            'exchange_index':r['exchange_index']})
    def cancel(self,oid,exchange_index=CRYPTO_EXCHANGE_INDEX):
        return self.client._request('DELETE','/portfolio/events/orders/'+oid,
                                    params={'exchange_index':exchange_index,'subaccount':0})

class Engine:
    def __init__(self,exchange,store,state_dir,live=False,clock=time.time,permission_check=None):
        self.exchange=exchange;self.store=store;self.root=Path(state_dir);self.live=live;self.clock=clock
        self.fault='';self.last_account=None
        self.permission_check=permission_check
    def halt_reason(self):
        if (self.root/'HALT').exists():return 'Manual HALT'
        if self.live and self.permission_check:
            reason=self.permission_check()
            if reason:return reason
        if self.fault:return self.fault
        # Cash belongs to a route. A poor balance on one route must not claim
        # another funded route is halted; entry_guard checks the selected route.
        return ''
    def account(self,exchange_index=CRYPTO_EXCHANGE_INDEX):
        started=self.clock()
        if hasattr(self.exchange,'account_snapshot'):
            raw_positions,raw_resting,balance=self.exchange.account_snapshot(exchange_index)
        else:
            raw_positions=self.exchange.positions()
            raw_resting=self.exchange.orders(status='resting')
            balance=self.exchange.balance() if exchange_index==2 else self.exchange.balance(exchange_index=exchange_index)
        positions={}
        for row in raw_positions:
            t=row.get('ticker','')
            if crypto(t):
                if row.get('exchange_index',route_for(dict(ticker=t)))!=route_for(dict(ticker=t)):
                    raise ValueError('Position exchange route mismatch')
                count=abs(number(row,'position_fp','position'))
                if count:positions[t]=count
        resting=[r for r in raw_resting if crypto(r.get('ticker',''))]
        balances=dict((self.last_account or {}).get('balances',{}));balances[str(exchange_index)]=str(balance)
        self.last_account={'ts':self.clock(),'balance':str(balance),'exchange_index':exchange_index,'balances':balances,'positions':{k:str(v) for k,v in positions.items()},'resting':len(resting),'read_duration_ms':1000*(self.clock()-started),'read_mode':'parallel_fresh_get' if getattr(self.exchange,'account_reader',None) else 'sequential'}
        return positions,resting,balance
    def entry_guard(self,s,positions,resting,balance):
        if self.halt_reason():return self.halt_reason()
        rule=lane_for(s)
        permission=asset_permission(self.root,record_asset(s))
        if permission:return permission
        if rule.name==Z3 and z3_permission(self.root):return z3_permission(self.root)
        if not self.live:return 'Observation only'
        if s.get('exchange_index')!=route_for(s):return 'Market exchange changed; review funding route'
        previous=self.store.get(s['ticker'])
        if previous and not self.retry_matches(previous,s):return 'Already attempted this market'
        rows=self.store.all()
        active=[r for r in rows if r['status'] in ACTIVE]
        if any(r['status'] in ('intent','unknown','ack','accounting_pending') for r in active):return 'Order reconciliation pending'
        if resting:return 'Crypto resting orders exist'
        reserved={r['ticker']:max(D(r.get('filled','0')),requested_count(r)) for r in active}
        for t,c in positions.items():reserved[t]=max(c,reserved.get(t,D('0')))
        if any(asset(t)==asset(s['ticker']) for t in reserved):return 'Asset already has exposure'
        if sum(reserved.values())+rule.contracts>MAX_CONTRACTS:return f'{MAX_CONTRACTS:g}-contract cap'
        # Different price caps require different fee-inclusive reservations.
        known={r['ticker']:r for r in active}
        projected=sum(c*(lane_for(known[t]).reserve if t in known else D('1'))
                      for t,c in reserved.items())+rule.contracts*rule.reserve
        if projected>MAX_RISK:return 'Exposure cap'
        realized=sum(D(r['pnl']) for r in rows if r.get('settled_day')==today() and r.get('pnl') is not None)
        if realized-projected < -DAILY_LOSS_BUDGET:return 'Daily loss budget including open risk'
        if rule.name==Z3:
            own=[r for r in active if lane_for(r).name==Z3]
            own_count=sum((max(D(r.get('filled','0')),requested_count(r)) for r in own),D('0'))
            own_risk=(own_count+rule.contracts)*rule.reserve
            if own_count+rule.contracts>Z3_MAX_CONTRACTS:return 'Strong cushion contract cap'
            if own_risk>Z3_MAX_RISK:return 'Strong cushion exposure cap'
            own_realized=sum((D(r['pnl']) for r in rows if lane_for(r).name==Z3 and
                              r.get('settled_day')==today() and r.get('pnl') is not None),D('0'))
            if own_realized-own_risk < -Z3_DAILY_BUDGET:return 'Strong cushion daily loss budget including open risk'
        if balance<MIN_CASH:return f'Exchange {route_for(s)} cash below ${MIN_CASH}; funding required'
        if balance<rule.contracts*rule.reserve:return 'Insufficient free cash'
        return ''
    def retry_matches(self,r,s):
        if fresh_retry_matches(r,s,self.clock()):return True
        if not retry_candidate(r,s['decision_ts'],self.clock(),lane_for(s).name):return False
        # Same-minute retries keep every signal input frozen.
        return all(r['signal'].get(k)==s.get(k) for k in
                   ('ticker','asset','side','decision_ts','close_ts','signal_bid','signal_ask','z',
                    'remaining_minutes','exchange_index','price_ranges'))
    def entered_markets(self,lane,decision_ts):
        # Excluding only eligible zero-fill rows forces a NEW signal calculation.
        return {ticker for ticker,r in self.store.entry_records()
                if not fresh_retry_candidate(r,decision_ts,self.clock(),lane)}
    def retry_signal(self,asset_name,lane,decision_ts):
        for _,r in self.store.entry_records():
            if r and r.get('asset')==asset_name and retry_candidate(r,decision_ts,self.clock(),lane):
                return dict(r['signal'])
        return None
    def confirm_zero_retry(self,r):
        """Fresh authoritative check of all previous attempts, not just local status."""
        history=self.store.attempts(r['ticker'])
        if len(history)!=r['attempt_number']:raise RuntimeError('Incomplete retry history')
        known={x['client_id'] for x in history}
        remote=self.exchange.orders(ticker=r['ticker'])
        if {x.get('client_order_id') for x in remote}!=known:
            raise RuntimeError('Unexpected or missing exchange market order; retry blocked')
        for prior in history:
            order=self.exchange.order(prior['order_id'])
            if (order.get('ticker')!=prior['ticker'] or order.get('client_order_id')!=prior['client_id'] or
                order.get('order_id')!=prior['order_id'] or order.get('status')!='canceled' or
                number(order,'initial_count_fp')!=requested_count(prior) or
                number(order,'fill_count_fp')!=0 or number(order,'remaining_count_fp')!=0):
                raise RuntimeError('Prior order is not confirmed canceled with zero fills')
            if self.exchange.fills(prior['order_id']):raise RuntimeError('Prior order has fills; retry blocked')
            for key in ('maker_fill_cost_dollars','taker_fill_cost_dollars','maker_fees_dollars','taker_fees_dollars'):
                if number(order,key)!=0:raise RuntimeError('Prior order has charged cash; retry blocked')
    def attempt(self,s):
        s['execution_started_ts']=self.clock()
        s['execution_executable']=False
        if s.get('status')!='paper_fill':return 'No signal'
        if not self.live:return 'Observation only'
        if not crypto(s.get('ticker','')) or s['side'] not in ('yes','no'):raise ValueError('unsupported market/side')
        rule=lane_for(s)
        floor=rule.floor-D('.00000001') if rule.name==Z3 else rule.floor
        if not (floor<=D(str(s['signal_ask']))<rule.ceiling and s['z']>=rule.threshold(s['remaining_minutes']) and rule.min_minutes<=s['remaining_minutes']<=rule.max_minutes):raise ValueError('signal gates failed')
        if not D('0')<=D(str(s['signal_bid']))<=D(str(s['signal_ask']))<=1:raise ValueError('invalid quote')
        if D(str(s['signal_ask']))-D(str(s['signal_bid']))>D('.02000001'):raise ValueError('spread failed')
        previous=self.store.get(s['ticker'])
        read_window=(RETRY_DEADLINE_SECONDS if previous else 35)-(self.clock()-s['decision_ts'])
        with get_budget(max(0,min(6.,read_window))):
            if previous:
                if not self.retry_matches(previous,s):return 'Already attempted this market'
                if self.clock()-previous['created_ts']<MIN_IOC_RETRY_INTERVAL_SECONDS:return 'Zero-fill retry cooldown'
                self.confirm_zero_retry(previous)
            account_started=self.clock()
            positions,resting,balance=self.account(route_for(s))
            s['execution_account_ms']=1000*(self.clock()-account_started)
            reason=self.entry_guard(s,positions,resting,balance)
            if reason:return reason
            yes,cap=limit_price(s)
            book=self.exchange.book(s['ticker'])
        s['execution_book_transport']=book.get('_transport',dict(source='rest'))
        s['execution_book_ts']=self.clock()
        s['execution_cap']=str(cap)
        if s.get('book_observed_ts') is not None:s['execution_book_gap_ms']=1000*(s['execution_book_ts']-s['book_observed_ts'])
        s['execution_book_levels']=book.get('no_dollars' if s['side']=='yes' else 'yes_dollars') or []
        levels=book.get('no_dollars' if s['side']=='yes' else 'yes_dollars') or []
        qty=D('0');cost=D('0')
        for p,n in sorted((1-D(p),D(n)) for p,n in levels):
            if n<=0:continue
            if not p.is_finite() or not n.is_finite() or not 0<=p<=1:raise ValueError('Invalid orderbook')
            if p<rule.floor:return 'Book weakened below floor'
            if p>cap:break
            take=min(D(rule.contracts)-qty,n);qty+=take;cost+=take*p
            if qty==rule.contracts:break
        if qty<rule.contracts:return 'Insufficient depth inside cap'
        age=self.clock()-s['decision_ts']
        if not 0<=age<=35:return 'Signal expired'
        if previous and age>RETRY_DEADLINE_SECONDS:return 'Zero-fill retry deadline passed'
        if self.halt_reason():return self.halt_reason()
        if rule.name==Z3 and z3_permission(self.root):return z3_permission(self.root)
        if asset_permission(self.root,record_asset(s)):return asset_permission(self.root,record_asset(s))
        s['execution_executable']=True
        s['execution_verified_ts']=self.clock()
        attempt_number=previous['attempt_number']+1 if previous else 1
        identity=(('kalshi-z3-v1:' if rule.name==Z3 else 'kalshi-z2-v1:')+s['ticker'])
        if previous:identity='kalshi-zero-retry-v1:'+s['ticker']+':'+str(attempt_number)
        r=dict(ticker=s['ticker'],side=s['side'],asset=s['asset'],lane=rule.name,
               status='intent',client_id=str(uuid.uuid5(uuid.NAMESPACE_URL,identity)),
               yes_limit=f'{yes:.4f}',favorite_limit=str(cap),exchange_index=s['exchange_index'],
               created_ts=self.clock(),close_ts=s['close_ts'],signal=s,filled='0',requested_count=str(rule.contracts),
               attempt_number=attempt_number,ioc_retry_version=1,ever_filled='0')
        reserve_started=time.monotonic()
        self.store.reserve(r,previous=previous)  # Every distinct intent is durable BEFORE POST.
        s['execution_reserve_ms']=1000*(time.monotonic()-reserve_started)
        # A slow disk commit or removed arm cannot turn a fresh intent into a stale order.
        final_age=self.clock()-s['decision_ts']
        final_block=self.halt_reason() or (z3_permission(self.root) if rule.name==Z3 else '') or asset_permission(self.root,record_asset(s))
        if not 0<=final_age<=(RETRY_DEADLINE_SECONDS if previous else 35):final_block='Signal expired before submission'
        if final_block:
            s['execution_executable']=False
            r.update(status='not_submitted',retry_forbidden=final_block,error=final_block)
            self.store.save(r);return final_block
        try:
            s['execution_submit_ts']=self.clock()
            result=self.exchange.submit(r)
            s['execution_ack_ts']=self.clock()
            if result.get('dry_run'):raise RuntimeError('Client suppressed live request')
            r['order_id']=result['order_id'];r['status']='ack'
        except Exception as e:
            s['execution_response_ts']=self.clock()
            r['status']='rejected' if getattr(e,'status',0) in (400,401,403,404,422,429) else 'unknown'
            r['error']=str(e)[:300]
            r['retry_forbidden']='Submission failed or ambiguous'
        self.store.save(r)
        return r['status']
    def reconcile_one(self,r):
        # A terminal fill with matching receipts cannot intentionally rest.
        # Keep its full risk reservation, but don't refetch immutable fills every
        # ten seconds before expiry. Recheck order/fills again at settlement.
        proof=r.get('terminal_fill_verified') or {}
        if (r['status']=='open' and proof.get('order_id')==r.get('order_id') and
                proof.get('filled')==r.get('filled') and proof.get('cost')==r.get('cost') and
                proof.get('fees')==r.get('fees') and D(r.get('filled','0'))>0 and
                self.clock()<r['close_ts']+30):return
        if r['status'] in ('intent','unknown'):
            r['retry_forbidden']='Recovered uncertain submission'
        if not r.get('order_id'):
            matches=[o for o in self.exchange.orders(ticker=r['ticker']) if o.get('client_order_id')==r['client_id']]
            if len(matches)>1:raise RuntimeError('Duplicate remote client id')
            if not matches:
                r['status']='unknown';self.store.save(r);return
            r['order_id']=matches[0]['order_id'];self.store.save(r)
        order=self.exchange.order(r['order_id'])
        if order.get('ticker')!=r['ticker'] or order.get('client_order_id')!=r['client_id']:raise RuntimeError('Order identity mismatch')
        filled=number(order,'fill_count_fp');remaining=number(order,'remaining_count_fp')
        requested=requested_count(r)
        if number(order,'initial_count_fp')!=requested or not 0<=filled<=requested or not 0<=remaining<=requested-filled:raise RuntimeError('Order quantity invariant violated')
        r['ever_filled']=str(max(D(r.get('ever_filled','0')),D(r.get('filled','0')),filled))
        r.pop('zero_fill_confirmation',None)
        # Persist evidence of any fill even if the following fills GET fails.
        if filled:self.store.save(r)
        if order.get('status')=='resting':
            r['retry_forbidden']='Unexpected resting order'
            self.store.save(r)  # A lost cancel response must not erase this restriction.
            if self.live:
                if route_for(r)==2:self.exchange.cancel(r['order_id'])
                else:self.exchange.cancel(r['order_id'],exchange_index=route_for(r))
            r['status']='ack';self.store.save(r);return # Confirm terminal state next poll.
        if order.get('status') not in ('executed','canceled'):raise RuntimeError('Order not terminal')
        if remaining!=0:raise RuntimeError('Terminal order has remaining quantity')
        receipts=getattr(self.exchange,'fill_receipts',None)
        fills=receipts(order,r) if receipts else self.exchange.fills(r['order_id']);unique={}
        for f in fills:
            if f['order_id']!=r['order_id']:raise RuntimeError('Fill order mismatch')
            if f.get('ticker',f.get('market_ticker'))!=r['ticker']:raise RuntimeError('Fill market mismatch')
            unique[f['fill_id']]=f
        count=D('0');cost=D('0');fees=D('0')
        for f in unique.values():
            n=number(f,'count_fp');px=number(f,'yes_price_dollars')
            if r['side']=='no':px=1-px
            charged=number(f,'fee_cost')
            if n<=0 or not 0<=px<=1 or charged<0:raise RuntimeError('Invalid fill economics')
            count+=n;cost+=n*px;fees+=charged
        if count>requested:raise RuntimeError('Overfill')
        r.update(filled=str(count),cost=str(cost),fees=str(fees))
        r['ever_filled']=str(max(D(r['ever_filled']),count))
        if count!=filled:
            r['status']='accounting_pending';self.store.save(r);return
        if not count:
            if order.get('status')=='canceled' and not unique and D(r['ever_filled'])==0:
                r['zero_fill_confirmation']=dict(order_id=r['order_id'],status='canceled',fill_count='0',remaining_count='0',fill_rows=0,ts=self.clock())
            r['status']='unfilled';self.store.save(r);return
        r['fill_transport']='websocket' if fills and all('ws_received_ts' in f for f in fills) else 'rest'
        if r['fill_transport']=='websocket':r['fill_stream_received_ts']=max(f['ws_received_ts'] for f in fills)
        # Settlement rechecks must not overwrite evidence of initial confirmation.
        if 'fill_confirmed_ts' not in r:
            r['initial_fill_transport']=r['fill_transport']
            if r['fill_transport']=='websocket':r['initial_fill_stream_received_ts']=r['fill_stream_received_ts']
        r['entry']=str(cost/count);r['status']='open'
        if cost/count<lane_for(r).floor:r['execution_warning']='Filled below entry band during price move'
        if cost/count>D(r['favorite_limit'])+D('.00001'):raise RuntimeError('Fill exceeded order price cap')
        r['terminal_fill_verified']=dict(order_id=r['order_id'],filled=str(count),cost=str(cost),fees=str(fees),ts=self.clock())
        r.setdefault('fill_confirmed_ts',self.clock())
        if self.clock()>=r['close_ts']+30:
            market=self.exchange.market(r['ticker'])
            if market.get('status') in ('settled','finalized') and market.get('result') in ('yes','no'):
                payout=count if market['result']==r['side'] else D('0')
                when=market.get('settlement_ts') or market['close_time']
                settled_dt=datetime.fromisoformat(when.replace('Z','+00:00')).astimezone(ET)
                r.update(status='settled',result=market['result'],pnl=str(payout-cost-fees),
                         settled_day=settled_dt.strftime('%Y-%m-%d'))
        self.store.save(r)
    def reconcile(self):
        errors=[]
        for r in self.store.all():
            if r['status'] not in ACTIVE:continue
            try:self.reconcile_one(r)
            except Exception as e:errors.append(str(e)[:300])
        self.fault='; '.join(errors)
        return errors
