"""Fresh parallel GETs for the single trading writer. No cached risk snapshots."""
from concurrent.futures import ThreadPoolExecutor, wait
from threading import Lock
from decimal import Decimal as D
import time
from kalshi_client import KalshiClientV2
import io_budget

ACCOUNT_READ_BUDGET_SECONDS = 3.0

class ReadClient(KalshiClientV2):
    def _request(self, method, path, **kwargs):
        if method != 'GET' or path not in ('/portfolio/positions','/portfolio/orders','/portfolio/balance'):
            raise RuntimeError('Account read worker rejects non-account GET')
        return super()._request(method, path, **kwargs)

class FreshAccountReads:
    def __init__(self, client, factory=ReadClient):
        # Each request role owns a separate persistent Session. Neither sessions
        # nor mutable engine/store state are shared between worker threads.
        self.clients=[factory(client.creds,base_url=client.base_url,dry_run=True,timeout=client.timeout) for _ in range(3)]
        self.pool=ThreadPoolExecutor(max_workers=3,thread_name_prefix='account-get')
        self.lock=Lock()
        self.last_timing=None
        self.pending=[]
        self.last_error=''

    @staticmethod
    def pages(client,path,key,**params):
        rows=[];cursor='';seen=set()
        while True:
            data=client._request('GET',path,params=dict(params,subaccount=0,limit=1000,**({'cursor':cursor} if cursor else {})))
            if not isinstance(data,dict) or key not in data:raise ValueError('Missing '+key)
            rows.extend(data[key]);cursor=data.get('cursor') or ''
            if not cursor:return rows
            if cursor in seen:raise ValueError('Pagination did not advance')
            seen.add(cursor)

    def balance(self,index):
        if index not in (0,2):raise ValueError('Unsupported balance route')
        data=self.clients[2]._request('GET','/portfolio/balance',params=dict(subaccount=0,exchange_index=index))
        value=D(str(data['balance_dollars'])) if data.get('balance_dollars') is not None else D(str(data['balance']))/100
        if not value.is_finite():raise ValueError('Non-finite balance')
        return value

    def snapshot(self,index):
        with self.lock:
            self.last_timing=None;started=time.time();tick=time.monotonic()
            # Timed-out HTTP/DNS work cannot be killed safely. Do not stack new
            # batches on it or ever publish its late/partial results.
            if self.pending and not all(f.done() for f in self.pending):
                raise TimeoutError('Previous account reads still draining; no new batch started')
            self.pending=[]
            left=io_budget.remaining()
            budget=min(ACCOUNT_READ_BUDGET_SECONDS,left) if left is not None else ACCOUNT_READ_BUDGET_SECONDS
            if budget<=.02:raise TimeoutError('Account read deadline exhausted')
            deadline=tick+budget
            def bounded(fn,*args,**kwargs):
                # ContextVars do not automatically propagate into pool workers.
                with io_budget.get_budget(max(0,deadline-time.monotonic())):
                    io_budget.timeout(1)
                    result=fn(*args,**kwargs)
                    io_budget.timeout(1)
                    return result
            futures=[self.pool.submit(bounded,self.pages,self.clients[0],'/portfolio/positions','market_positions'),
                     self.pool.submit(bounded,self.pages,self.clients[1],'/portfolio/orders','orders',status='resting'),
                     self.pool.submit(bounded,self.balance,index)]
            self.pending=futures
            _,unfinished=wait(futures,timeout=max(0,deadline-time.monotonic()))
            if unfinished or time.monotonic()>=deadline:
                for future in unfinished:future.cancel()
                self.last_error='Account read deadline exceeded; late results discarded'
                raise TimeoutError(self.last_error)
            try:
                result=tuple(f.result() for f in futures)
            except Exception as exc:
                self.last_error=str(exc)[:200]
                raise
            finally:
                self.pending=[]
            self.last_error=''
            self.last_timing=dict(started=started,completed=time.time(),duration_ms=1000*(time.monotonic()-tick),mode='parallel_fresh_get')
            return result

    def close(self):
        self.pool.shutdown(wait=True)
        for client in self.clients:client.session.close()
