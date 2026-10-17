"""Two-contract execution state machine. Construct with live=False by default.

Single IOC attempt per market. A failed/ambiguous POST is NEVER repeated.
SQLite commits intent before submission; exchange orders/fills resolve state.
"""
from __future__ import annotations
from datetime import datetime
from decimal import Decimal, ROUND_FLOOR, ROUND_CEILING
import json
import sqlite3
import time
import uuid
from pathlib import Path
from zoneinfo import ZoneInfo

D=Decimal
ASSETS=('BTC','ETH','SOL','XRP')
SERIES=tuple('KX'+a+'15M' for a in ASSETS)
ET=ZoneInfo('America/New_York')
ACTIVE=('intent','unknown','ack','open','accounting_pending')
from sizing import CONTRACTS, MAX_CONTRACTS, MAX_RISK, DAILY_LOSS_BUDGET, MIN_CASH
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
    if not n.is_finite() or n not in (D('1'), D('2')):
        raise ValueError('Invalid requested count')
    return n

def crypto(ticker):return any(ticker.startswith(s+'-') for s in SERIES)
def asset(ticker):return ticker.split('-')[0]
def today():return datetime.now(ET).strftime('%Y-%m-%d')

def limit_price(signal):
    """Round toward less spending on the market's YES-perspective tick ladder."""
    cap=min(D(str(signal['signal_ask']))+D('.01'),D('.939'))
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
            if not D('.85')<=spend<=cap:raise ValueError('price cannot fit band and ticks')
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
        self.db.commit()
    def all(self):return [json.loads(x[0]) for x in self.db.execute('SELECT record FROM orders ORDER BY rowid')]
    def get(self,ticker):
        row=self.db.execute('SELECT record FROM orders WHERE ticker=?',(ticker,)).fetchone()
        return json.loads(row[0]) if row else None
    def save(self,r):
        self.db.execute('INSERT INTO orders VALUES (?,?) ON CONFLICT(ticker) DO UPDATE SET record=excluded.record',(r['ticker'],json.dumps(r)))
        self.db.commit()
    def reserve(self,r):
        # UNIQUE market key also protects accidental duplicate software calls.
        self.db.execute('INSERT INTO orders VALUES (?,?)',(r['ticker'],json.dumps(r)));self.db.commit()
    def event(self,r):
        self.db.execute('INSERT INTO events(ts,event) VALUES (?,?)',(time.time(),json.dumps(r)));self.db.commit()

class Exchange:
    def __init__(self,client):self.client=client
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
    def balance(self):
        r=self.client._request('GET','/portfolio/balance',params={'subaccount':0,'exchange_index':CRYPTO_EXCHANGE_INDEX})
        return number(r,'balance_dollars') if r.get('balance_dollars') is not None else number(r,'balance')/100
    def market(self,ticker):return self.client.get_market(ticker)['market']
    def book(self,ticker):return self.client.get_orderbook(ticker,depth=10)['orderbook_fp']
    def submit(self,r):
        return self.client._request('POST','/portfolio/events/orders',body={
            'ticker':r['ticker'],'client_order_id':r['client_id'],
            'side':'bid' if r['side']=='yes' else 'ask',
            'count':f'{requested_count(r):.2f}','price':r['yes_limit'],'time_in_force':'immediate_or_cancel',
            'self_trade_prevention_type':'taker_at_cross','post_only':False,
            'cancel_order_on_pause':True,'reduce_only':False,'subaccount':0,
            'exchange_index':r['exchange_index']})
    def cancel(self,oid):
        return self.client._request('DELETE','/portfolio/events/orders/'+oid,
                                    params={'exchange_index':CRYPTO_EXCHANGE_INDEX,'subaccount':0})

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
        if self.last_account and D(self.last_account['balance'])<MIN_CASH:return f'Crypto exchange 2 cash below ${MIN_CASH}; funding required'
        return ''
    def account(self):
        positions={}
        for row in self.exchange.positions():
            t=row.get('ticker','')
            if crypto(t):
                count=abs(number(row,'position_fp','position'))
                if count:positions[t]=count
        resting=[r for r in self.exchange.orders(status='resting') if crypto(r.get('ticker',''))]
        balance=self.exchange.balance()
        self.last_account={'ts':self.clock(),'balance':str(balance),'exchange_index':CRYPTO_EXCHANGE_INDEX,'positions':{k:str(v) for k,v in positions.items()},'resting':len(resting)}
        return positions,resting,balance
    def entry_guard(self,s,positions,resting,balance):
        if self.halt_reason():return self.halt_reason()
        if not self.live:return 'Observation only'
        if s.get('exchange_index')!=CRYPTO_EXCHANGE_INDEX:return 'Market exchange changed; review funding route'
        if self.store.get(s['ticker']):return 'Already attempted this market'
        active=[r for r in self.store.all() if r['status'] in ACTIVE]
        if any(r['status'] in ('intent','unknown','ack','accounting_pending') for r in active):return 'Order reconciliation pending'
        if resting:return 'Crypto resting orders exist'
        reserved={r['ticker']:max(D(r.get('filled','0')),requested_count(r)) for r in active}
        for t,c in positions.items():reserved[t]=max(c,reserved.get(t,D('0')))
        if any(asset(t)==asset(s['ticker']) for t in reserved):return 'Asset already has exposure'
        if sum(reserved.values())+CONTRACTS>MAX_CONTRACTS:return 'Eight-contract cap'
        # Reserve $0.95 per contract, including its maximal entry fee.
        projected=(sum(reserved.values())+CONTRACTS)*D('.95')
        if projected>MAX_RISK:return 'Exposure cap'
        realized=sum(D(r['pnl']) for r in self.store.all() if r.get('settled_day')==today() and r.get('pnl') is not None)
        if realized-projected < -DAILY_LOSS_BUDGET:return 'Daily loss budget including open risk'
        if balance<MIN_CASH:return 'Insufficient free cash'
        return ''
    def attempt(self,s):
        if s.get('status')!='paper_fill':return 'No signal'
        if not self.live:return 'Observation only'
        if not crypto(s.get('ticker','')) or s['side'] not in ('yes','no'):raise ValueError('unsupported market/side')
        if not (D('.85')<=D(str(s['signal_ask']))<D('.93') and s['z']>=2 and 3<=s['remaining_minutes']<=10):raise ValueError('signal gates failed')
        if D(str(s['signal_ask']))-D(str(s['signal_bid']))>D('.02000001'):raise ValueError('spread failed')
        positions,resting,balance=self.account()
        reason=self.entry_guard(s,positions,resting,balance)
        if reason:return reason
        yes,cap=limit_price(s)
        book=self.exchange.book(s['ticker'])
        levels=book.get('no_dollars' if s['side']=='yes' else 'yes_dollars') or []
        qty=D('0');cost=D('0')
        for p,n in sorted((1-D(p),D(n)) for p,n in levels):
            if n<=0:continue
            if p<D('.85'):return 'Book weakened below floor'
            if p>cap:break
            take=min(D(CONTRACTS)-qty,n);qty+=take;cost+=take*p
            if qty==CONTRACTS:break
        if qty<CONTRACTS:return 'Insufficient depth inside cap'
        age=self.clock()-s['decision_ts']
        if not 0<=age<=35:return 'Signal expired'
        if self.halt_reason():return self.halt_reason()
        r=dict(ticker=s['ticker'],side=s['side'],asset=s['asset'],
               status='intent',client_id=str(uuid.uuid5(uuid.NAMESPACE_URL,'kalshi-z2-v1:'+s['ticker'])),
               yes_limit=f'{yes:.4f}',favorite_limit=str(cap),exchange_index=s['exchange_index'],
               created_ts=self.clock(),close_ts=s['close_ts'],signal=s,filled='0',requested_count=str(CONTRACTS))
        self.store.reserve(r)  # Durable BEFORE the only POST.
        try:
            result=self.exchange.submit(r)
            if result.get('dry_run'):raise RuntimeError('Client suppressed live request')
            r['order_id']=result['order_id'];r['status']='ack'
        except Exception as e:
            r['status']='rejected' if getattr(e,'status',0) in (400,401,403,404,422,429) else 'unknown'
            r['error']=str(e)[:300]
        self.store.save(r)
        return r['status']
    def reconcile_one(self,r):
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
        if order.get('status')=='resting':
            if self.live:self.exchange.cancel(r['order_id'])
            r['status']='ack';self.store.save(r);return # Confirm terminal state next poll.
        if order.get('status') not in ('executed','canceled'):raise RuntimeError('Order not terminal')
        fills=self.exchange.fills(r['order_id']);unique={}
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
        if count!=filled:
            r['status']='accounting_pending';self.store.save(r);return
        if not count:
            r['status']='unfilled';self.store.save(r);return
        r['entry']=str(cost/count);r['status']='open'
        if cost/count<D('.85'):r['execution_warning']='Filled below entry band during price move'
        if cost/count>D(r['favorite_limit'])+D('.00001'):raise RuntimeError('Fill exceeded order price cap')
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
