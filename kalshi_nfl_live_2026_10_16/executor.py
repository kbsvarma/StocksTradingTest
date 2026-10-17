"""Staged NFL-only IOC executor; unavailable without a fresh verification token.

No financial mutation occurs on import or in preflight. Ambiguous submissions
remain reserved and are reconciled by client ID; they are never resubmitted.
"""
import asyncio,hashlib,json,time
from pathlib import Path
from core import canonical,dec,depth,leader,current_leader,state_at,order_payload,ts,digest
from kalshi_client import KalshiClient,Credentials
from stream_transport import FillState

def bundle_hash(root):
    names=('core.py','store.py','observer.py','executor.py','review.py','stream_transport.py','kalshi_client.py','io_budget.py','config.json')
    return digest({n:hashlib.sha256((root/n).read_bytes()).hexdigest() for n in names})

def validated_terminal(order,pick,client_id,oid,fills):
    if (order.get('order_id')!=oid or order.get('ticker')!=pick['ticker'] or order.get('client_order_id')!=client_id or
        order.get('outcome_side')!=pick['side'] or order.get('book_side')!=('bid' if pick['side']=='yes' else 'ask') or
        order.get('exchange_index')!=0 or order.get('subaccount_number',order.get('subaccount',0))!=0):raise ValueError('order identity/direction/route')
    if order['status'] not in ('executed','canceled') or dec(order['remaining_count_fp'])!=0 or dec(order['initial_count_fp'])!=5:raise ValueError('nonterminal or invalid quantity')
    n=dec(order['fill_count_fp']);cost=sum(dec(order[k]) for k in ('maker_fill_cost_dollars','taker_fill_cost_dollars'));fees=sum(dec(order[k]) for k in ('maker_fees_dollars','taker_fees_dollars'))
    if not 0<=n<=5 or cost<0 or fees<0:raise ValueError('invalid economics')
    unique={}
    for f in fills:
        fid=f.get('fill_id',f.get('trade_id'))
        if not fid or f.get('order_id')!=oid or f.get('ticker',f.get('market_ticker'))!=pick['ticker']:raise ValueError('fill identity')
        if fid in unique and unique[fid]!=f:raise ValueError('conflicting duplicate fill')
        unique[fid]=f
    total=fcost=ffees=dec(0)
    for f in unique.values():
        q=dec(f['count_fp']);p=dec(f['yes_price_dollars']);charged=dec(f['fee_cost'])
        if q<=0 or not 0<=p<=1 or charged<0:raise ValueError('invalid fill')
        p=p if pick['side']=='yes' else 1-p
        if p>dec(pick['cap']):raise ValueError('execution exceeded limit')
        total+=q;fcost+=q*p;ffees+=charged
    if (total,fcost,ffees)!=(n,cost,fees):raise ValueError('incomplete fill receipts')
    if n==0 and (order['status']!='canceled' or unique or cost or fees):raise ValueError('not a confirmed zero fill')
    return dict(status='filled' if n else 'zero',quantity=str(n),cost=str(cost+fees),fees=str(fees),reserve=str(cost+fees))

class Executor:
    def __init__(self,observer):
        self.o=observer;self.store=observer.store;self.db=self.store.db;self.root=observer.root;self.cfg=observer.cfg
        self.client=KalshiClient(Credentials.load(),base_url='https://external-api.kalshi.com',dry_run=True,timeout=2)
        self.account=None;self.processing=set();self.hash=bundle_hash(self.root)
        # Even a pending intent that crashed before POST is ambiguous on recovery.
        with self.db:self.db.execute("UPDATE orders SET status='unknown' WHERE mode='live' AND status='pending'")
    def armed(self):
        try:
            token=json.loads((self.root/'ARMED.json').read_text())
            return token['bundle_hash']==self.hash and token['config_hash']==digest(self.cfg) and token['expires']>time.time() and not (self.root/'HALT').exists()
        except (OSError,KeyError,ValueError,TypeError):return False
    async def get(self,path,params=None):return await asyncio.to_thread(self.client._request,'GET',path,params=params)
    async def write(self,method,path,**kwargs):
        # A dedicated session keeps one in-flight write from arming the read client.
        client=KalshiClient(self.client.creds,base_url='https://external-api.kalshi.com',dry_run=False,timeout=2)
        try:return await asyncio.to_thread(client._request,method,path,**kwargs)
        finally:client.session.close()
    async def pages(self,path,key,params=None):
        rows=[];cursor='';seen=set()
        while True:
            r=await self.get(path,dict(params or {},limit=1000,**({'cursor':cursor} if cursor else {})))
            rows+=r[key];cursor=r.get('cursor','')
            if not cursor:return rows
            if cursor in seen:raise ValueError('pagination repeated')
            seen.add(cursor)
    async def refresh_account(self):
        start=time.time();b=await self.get('/portfolio/balance',{'subaccount':0,'exchange_index':0})
        positions=await self.pages('/portfolio/positions','market_positions',{'subaccount':0})
        resting=await self.pages('/portfolio/orders','orders',{'subaccount':0,'status':'resting'})
        cash=dec(b['balance_dollars']) if 'balance_dollars' in b else dec(b['balance'])/100
        if time.time()-start>3:raise ValueError('account snapshot too slow')
        self.account=dict(at=time.time(),start=start,cash=str(cash),positions=[p for p in positions if p['ticker'].startswith('KXNFLGAME-')],resting=[p for p in resting if p['ticker'].startswith('KXNFLGAME-')],duration=time.time()-start)
        self.store.setmeta('account',self.account)
        return self.account
    def account_gate(self,required):
        a=self.account
        if not a or time.time()-a['start']>7:raise ValueError('account stale')
        # Protect an additional $25 of shared engine cash for existing bots.
        pending=sum(dec(r[0]) for r in self.db.execute("SELECT reserve FROM orders WHERE mode='live' AND status IN ('pending','unknown')"))
        if dec(a['cash'])-pending<dec('25')+dec('2'):raise ValueError('protected shared cash buffer')
        owned={r[0] for r in self.db.execute("SELECT ticker FROM orders WHERE mode='live' AND status IN ('filled','settled')")}
        if any(dec(p.get('position_fp',p.get('position',0)))!=0 and p['ticker'] not in owned for p in a['positions']):raise ValueError('unowned NFL position')
        if a['resting']:raise ValueError('unexpected NFL resting order')
    async def submit(self,ident,g):
        self.processing.add(ident);sent=False
        try:
            row=self.db.execute('SELECT * FROM orders WHERE id=?',(ident,)).fetchone();p=json.loads(row['data'])
            if row['status']!='pending' or row['mode']!='live':return
            if not self.armed() or not self.o.ntp or time.time()-self.o.clock_checked>45:raise ValueError('not armed or clock invalid')
            report=self.o.last_report
            if time.time()-report.get('heartbeat',0)>10 or not report.get('operational_checks_pass'):raise ValueError('verification not current')
            gamecheck=next((x for x in report['games'] if x['event']==g['event']),{})
            if not gamecheck.get('operational_checks_pass'):raise ValueError('this game feed not verified')
            self.account_gate(dec(row['reserve']))
            # Exchange status and contract status may change after catalogue discovery.
            m=(await self.get('/markets/'+p['ticker']))['market']
            if m.get('status')!='active' or m.get('exchange_index')!=0:raise ValueError('market no longer active')
            versions,snap=self.store.source_at(g['event'],time.time());state=state_at(versions,time.time(),self.cfg)
            if not snap or leader(state)!=leader(p['state']) or current_leader(versions,time.time())!=leader(p['state']):raise ValueError('score changed or stale')
            if time.time()-p['boundary']>self.cfg['execution_deadline_seconds'] or time.time()>=ts(self.cfg['entry_cutoff']):raise ValueError('execution deadline from signal minute')
            fill,why=depth(self.o.stream.book(p['ticker']),p['side'],p['cap'])
            if why:raise ValueError(why)
            payload=order_payload(p,row['client_id']);p['submission']=dict(payload=payload,at=time.time(),book=fill)
            # Save before sending; any exception from this point requires reconciliation.
            with self.db:self.db.execute("UPDATE orders SET status='unknown',data=? WHERE id=?",(canonical(p),ident))
            sent=True;ack=await self.write('POST','/portfolio/events/orders',body=payload)
            if ack.get('dry_run') or not ack.get('order_id'):raise ValueError('ambiguous acknowledgement')
            p['order_id']=ack['order_id'];p['ack']=ack;p['ack_received']=time.time()
            with self.db:self.db.execute('UPDATE orders SET data=? WHERE id=?',(canonical(p),ident))
            await self.reconcile(ident)
        except Exception as e:
            self.store.log('live_submit_error',g['event'],dict(id=ident,sent=sent,error=str(e)))
            if not sent:
                with self.db:self.db.execute("UPDATE orders SET status='zero',reserve='0' WHERE id=? AND status='pending'",(ident,))
        finally:self.processing.discard(ident)
    async def reconcile(self,ident):
        row=self.db.execute('SELECT * FROM orders WHERE id=?',(ident,)).fetchone();p=json.loads(row['data']);oid=p.get('order_id')
        if not oid:
            matches=[r for r in await self.pages('/portfolio/orders','orders',dict(ticker=p['ticker'],subaccount=0)) if r.get('client_order_id')==row['client_id']]
            if len(matches)!=1:raise ValueError('unknown submission; no unique order found')
            oid=matches[0]['order_id'];p['order_id']=oid
            with self.db:self.db.execute('UPDATE orders SET data=? WHERE id=?',(canonical(p),ident))
        order=(await self.get('/portfolio/orders/'+oid))['order']
        if order.get('status')=='resting':
            # Cancel only this durable, identity-matched NFL intent. Never all orders.
            if order.get('client_order_id')!=row['client_id'] or order.get('ticker')!=p['ticker'] or order.get('exchange_index')!=0:raise ValueError('resting order identity mismatch')
            if not p.get('cancel_attempted'):
                p['cancel_attempted']=time.time()
                with self.db:self.db.execute('UPDATE orders SET data=? WHERE id=?',(canonical(p),ident))
                await self.write('DELETE','/portfolio/events/orders/'+oid,params=dict(subaccount=0,exchange_index=0))
            raise ValueError('resting order cancellation pending')
        record=dict(p,order_id=oid,client_id=row['client_id'])
        fills=self.o.stream.confirmed_fills(order,record)
        transport='websocket' if fills is not None else 'rest'
        if fills is None:fills=await self.pages('/portfolio/fills','fills',dict(order_id=oid,subaccount=0))
        result=validated_terminal(order,p,row['client_id'],oid,fills)
        p.update(terminal_order=order,fill_receipts=fills,fill_transport=transport,confirmed_at=time.time())
        with self.db:self.db.execute('UPDATE orders SET status=?,quantity=?,cost=?,fees=?,reserve=?,data=? WHERE id=?',(result['status'],result['quantity'],result['cost'],result['fees'],result['reserve'],canonical(p),ident))
    async def run(self):
        while self.o.running:
            if self.armed():
                try:await self.refresh_account()
                except Exception as e:self.account=None;self.store.log('account_error','',str(e))
            for r in self.db.execute("SELECT id,event FROM orders WHERE mode='live' AND status='unknown'").fetchall():
                if r['id'] in self.processing:continue
                try:await self.reconcile(r['id'])
                except Exception as e:self.store.log('reconciliation_pending',r['event'],str(e))
            await asyncio.sleep(3)
