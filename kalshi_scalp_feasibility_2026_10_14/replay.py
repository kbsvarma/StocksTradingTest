"""Isolated causal paper scalp simulation. No network or trading imports."""
from pathlib import Path
import collections,datetime,gzip,hashlib,json,math,pickle,statistics,time
from decimal import Decimal as D,ROUND_CEILING,ROUND_FLOOR
from zoneinfo import ZoneInfo
P=Path(__file__).resolve().parent
SPLIT=1791072000000 # 2026-10-04 00:00 UTC
def down(p):return (p//100)*100 if 1000<=p<9000 else p//10*10
def up(p):return math.ceil(p/100)*100 if 1000<p<=9000 else math.ceil(p/10)*10
def qty_at(levels,p):
    if len(levels)>=10 and p<levels[-1][0]:return None
    return next((q for x,q in levels if x==p),0)
def bid_ask(row,side):
    own,opp=(row[3],row[4]) if side==1 else (row[4],row[3]);return own,opp,own[0][0],10000-opp[0][0]
def maker_fill(order,src,side,price,qty):
    if not order or not order['accepted']<=src<order['until'] or qty<=0:return 0
    if side!=order['aggressor']:return 0
    favorable=price<order['price'] if order['buy'] else price>order['price']
    if favorable:
        order['queue']=0
        return min(qty,order['remaining'])
    if price!=order['price']:return 0
    ahead=min(qty,order['queue']);order['queue']-=ahead
    return min(qty-ahead,order['remaining'])
def account(legs):
    groups=collections.defaultdict(lambda:[D(0),D(0),True])
    for leg in legs:
        g=groups[leg['order']];q=D(leg['qty'])/100;p=D(leg['price'])/10000;g[0]+=q*p;g[1]+=D(0) if leg['maker'] else D('.07')*q*p*(1-p);g[2]=leg['buy']
    net=fees=rounding=gross=D(0)
    for cost,f,buy in groups.values():
        f=f.quantize(D('.000001'),rounding=ROUND_CEILING);fees+=f;gross+=-cost if buy else cost
        cash=-(cost+f).quantize(D('.0001'),rounding=ROUND_CEILING) if buy else (cost-f).quantize(D('.0001'),rounding=ROUND_FLOOR)
        net+=cash;rounding+=(-cost-f if buy else cost-f)-cash
    return dict(net=float(net),fees=float(fees),cash_rounding=float(rounding),gross=float(gross))
class Run:
    def __init__(self,target=200,timeout=60000,stable=True,latency=250,budget=True):
        self.target=target;self.timeout=timeout;self.stable=stable;self.L=latency;self.budget=budget;self.name=f'{"stable" if stable else "diponly"}_tp{target//100}_t{timeout//1000}_L{latency}_{"cash100" if budget else "uncapped"}'
        self.cash=100.;self.daynet=collections.defaultdict(float);self.states={};self.ledger=[];self.rejections=collections.Counter();self.buckets=collections.Counter();self.counter=0
    def new_market(self,meta):
        assert all(not s.get('o') for s in self.states.values()),'Position crossed capture without closing'
        self.meta=meta;self.states={a:dict(o=None,cooldown=0,book=None,last=0) for a in meta}
    def day(self,t):return datetime.datetime.fromtimestamp(t/1000,ZoneInfo('America/New_York')).date().isoformat()
    def reserve(self):return sum((s['o']['price']/10000)*5 for s in self.states.values() if s.get('o'))
    def finish(self,a,now,reason):
        s=self.states[a];o=s['o'];o['ended']=now;o['end_reason']=reason;o['quantity']=o['bought']/100;o['remaining_qty']=o['inventory']/100
        z=account(o['legs']);o.update(z);assert o['inventory']==0 and o['bought']==o['sold'];self.cash+=o['net'];self.daynet[self.day(now)]+=o['net'];o['cash_after']=self.cash
        o['stress025']=round(o['net']-sum(x['qty'] for x in o['legs'])/100*.0025,8);o['stress1c']=round(o['net']-o['quantity']*.01,8)
        self.ledger.append(o);s['o']=None;s['cooldown']=now+10000
    def signal(self,row,side):
        own,opp,bid,ask=bid_ask(row,side);now=row[0];a=row[2];rem=self.meta[a]['close_ms']-now
        if not 120000<=rem<=720000 or not 2000<=ask<=9500 or not 100<=ask-bid<=400 or row[9] is None:return None
        mid=(bid+ask)/2;old=row[9] if side==1 else 10000-row[9]
        if old-mid<200:return None
        buy,sell=(row[10],row[11]) if side==1 else (row[11],row[10])
        if sell<500 or sell<=buy:return None
        # Matched data coverage isolates the stability filter, rather than missing benchmarks.
        if row[7] is None or row[8] is None:return None
        if self.stable and (abs(row[7])>.01 or row[8]>.02):return None
        price=down(ask-100)
        if price<bid or price>=ask:return None
        queue=qty_at(own,price)
        if queue is None:return None
        return dict(price=price,initial_queue=queue,side=side,signal_bid=bid,signal_ask=ask,mid_drop=old-mid,fair_change=row[7],fair_range=row[8])
    def start(self,row,f):
        now=row[0];a=row[2];s=self.states[a];self.buckets['qualified_candidates']+=1;reserve=f['price']/10000*5
        if self.budget and (self.cash-self.reserve()<reserve+2 or self.reserve()+reserve>25 or max(0,-self.daynet[self.day(now)])+self.reserve()+reserve>20):
            self.rejections['budget']+=1;s['cooldown']=now+10000;return
        self.counter+=1;s['o']=dict(**f,id=self.counter,asset='BTC' if a==0 else 'ETH',ticker=self.meta[a]['ticker'],signal_ts=now,phase='entry_pending',due=now+self.L,entry=None,exit=None,bought=0,sold=0,inventory=0,legs=[],first_fill=None,first_fill_source=None,entry_reject=None,forced_reason=None,force_number=0,coverage_unknown=False,max_trade_delay=0,run=self.name)
    def leg(self,o,now,source,qty,price,buy,maker,order,trade_id=None):
        assert qty>0
        if buy:o['bought']+=qty;o['inventory']+=qty
        else:
            assert qty<=o['inventory'];o['sold']+=qty;o['inventory']-=qty
        o['legs'].append(dict(t=now,source=source,qty=qty,price=price,buy=buy,maker=maker,order=order,trade_id=trade_id))
    def force(self,o,now,reason):
        if o['phase']=='liquidating':return
        for k in ('entry','exit'):
            if o.get(k):o[k]['until']=min(o[k]['until'],now+self.L)
        o['phase']='liquidating';o['due']=now+2*self.L;o['forced_reason']=reason;o['trigger_ts']=now
    def sell_depth(self,o,row,minprice=0,order='force'):
        own,opp,bid,ask=bid_ask(row,o['side']);want=o['inventory'];taken=0
        consumed=o.setdefault('consumed_taker_depth',{})
        for price,qty in own:
            if price<minprice or not want:break
            q=min(want,max(0,qty-consumed.get(price,0)))
            if not q:continue
            self.leg(o,row[0],row[0],q,price,False,False,order);taken+=q;want-=q;consumed[price]=consumed.get(price,0)+q
        return taken
    def book(self,row):
        now=row[0];a=row[2];s=self.states[a];prior=s['last'];s['last']=now;s['book']=row;o=s['o']
        valid=row[5]<=2000
        if o and (not valid or (prior and now-prior>2000)):
            o['coverage_unknown']=True;self.force(o,now,'feed_gap')
        if o is None:
            if not valid or now<s['cooldown']:return
            for side in (1,-1):
                f=self.signal(row,side)
                if f:self.start(row,f);break
            return
        own,opp,bid,ask=bid_ask(row,o['side'])
        if o['phase']=='entry_pending' and now>=o['due']:
            q=qty_at(own,o['price'])
            if ask<=o['price'] or q is None or not valid:
                o['entry_reject']='post_only_cross' if ask<=o['price'] else 'queue_or_feed_unknown';self.finish(a,now,o['entry_reject']);return
            o['entry']=dict(buy=True,aggressor=-o['side'],price=o['price'],queue=max(q,o['initial_queue']),remaining=500,accepted=now,until=now+10000);o['accepted_ts']=now;o['phase']='entry_rest'
        if o['phase']=='entry_rest':
            # Passive order expires at known exchange deadline. Do not extend on polling.
            if now>=o['entry']['until']+self.L:
                if o['inventory']==0:self.finish(a,now,'entry_expired');return
                o['phase']='exit_pending';o['due']=now+self.L
        if o['inventory'] and o['phase']!='liquidating':
            if bid<=o['price']-500:self.force(o,now,'stop')
            elif now>=o['first_fill']+self.timeout:self.force(o,now,'timeout')
            elif now>=self.meta[a]['close_ms']-10000:self.force(o,now,'end_of_market')
        if o['phase']=='exit_pending' and now>=o['due']:
            target=up(o['price']+self.target);q=qty_at(opp,10000-target)
            if target>=10000 or q is None or not valid:
                o['coverage_unknown']=True
                self.force(o,now,'exit_queue_unknown')
            else:
                if bid>=target:self.sell_depth(o,row,target,'target_taker')
                if o['inventory']==0:self.finish(a,now,'target_marketable');return
                o['exit']=dict(buy=False,aggressor=o['side'],price=target,queue=q,remaining=o['inventory'],accepted=now,until=10**20);o['phase']='target_rest';o['exit_accepted']=now
        if o['phase']=='liquidating' and now>=o['due'] and valid:
            if o['inventory']:
                o['force_number']+=1;self.sell_depth(o,row,0,'force'+str(o['force_number']))
            if not o['inventory']:self.finish(a,now,o['forced_reason']);return
            o['due']=now+self.L
    def trade(self,row):
        now,_,a,src,aggressor,yesprice,qty,tid=row;s=self.states[a];o=s['o']
        if not o:return
        o['max_trade_delay']=max(o['max_trade_delay'],now-src)
        price=yesprice if o['side']==1 else 10000-yesprice
        q=maker_fill(o['entry'],src,aggressor,price,qty)
        if q:
            assert o['bought']+q<=500;o['entry']['remaining']-=q;self.leg(o,now,src,q,o['price'],True,True,'entry',tid)
            if o['first_fill'] is None:
                o['first_fill']=now;o['first_fill_source']=src;o['entry']['until']=min(o['entry']['until'],now+self.L);o['phase']='exit_pending';o['due']=now+2*self.L
            elif o['exit'] and o['exit']['remaining']<o['inventory']:
                # A delayed cancellation-race entry cannot be silently sold by an undersized target.
                o['coverage_unknown']=True;self.force(o,now,'late_entry_reconciliation')
        q=maker_fill(o['exit'],src,aggressor,price,qty)
        if q:
            q=min(q,o['inventory']);o['exit']['remaining']-=q;self.leg(o,now,src,q,o['exit']['price'],False,True,'target',tid)
            if not o['inventory']:self.finish(a,now,'target');return
    def end_market(self,now):
        for a,s in self.states.items():
            if not s['o']:continue
            o=s['o']
            if not o['inventory']:self.finish(a,now,'capture_end_no_fill');continue
            # Explicit worst-case write-down of unresolved inventory; never drop failed positions.
            o['coverage_unknown']=True;self.leg(o,now,now,o['inventory'],0,False,True,'unresolved_write_down');self.finish(a,now,'unresolved_write_down')
def metrics(rows):
    f=[r for r in rows if r['quantity']>0];cum=peak=dd=0.
    for r in sorted(f,key=lambda x:x['ended']):cum+=r['net'];peak=max(peak,cum);dd=max(dd,peak-cum)
    return dict(attempts=len(rows),filled=len(f),unfilled=len(rows)-len(f),contracts=round(sum(r['quantity'] for r in f),2),partial_entries=sum(r['quantity']<5 for r in f),wins=sum(r['net']>0 for r in f),losses=sum(r['net']<0 for r in f),flat=sum(r['net']==0 for r in f),net=round(sum(r['net'] for r in f),6),gross=round(sum(r['gross'] for r in f),6),fees=round(sum(r['fees'] for r in f),6),rounding=round(sum(r['cash_rounding'] for r in f),6),stress025=round(sum(r['stress025'] for r in f),6),stress1c=round(sum(r['stress1c'] for r in f),6),drawdown=round(dd,6),exit_reasons=dict(collections.Counter(r['end_reason'] for r in f)),rejections=dict(collections.Counter(r['end_reason'] for r in rows if not r['quantity'])),unknown_filled=sum(r['coverage_unknown'] for r in f),mean_hold_s=statistics.mean((r['ended']-r['first_fill'])/1000 for r in f) if f else None,max_loss=min((r['net'] for r in f),default=0))
def main():
    start=time.time();runs=[Run(t,timeout,stable,latency) for stable in (True,False) for t in (200,300) for timeout in (30000,60000) for latency in (250,1000)]
    runs.extend(Run(t,timeout,True,250,False) for t in (200,300) for timeout in (30000,60000))
    runs.append(Run(200,60000,False,250,False))
    manifest=json.loads((P/'capture_manifest.json').read_text());chosen={}
    assert len(manifest)==len(list(Path('/home/varma/kalshi_btc_eth_quant_2026_10_02/forward_state/data').glob('*.gz'))),'Extraction is not complete'
    for r in manifest:
        if r['accepted']:
            close=int(r['file'].split('_')[1]);chosen.setdefault(close,r)
    coverage=[]
    for i,(close,info) in enumerate(sorted(chosen.items())):
        with gzip.open(P/'compact'/info['target'],'rb') as inp:data=pickle.load(inp)
        for run in runs:run.new_market(data['meta'])
        for row in data['rows']:
            for run in runs:
                if row[1]==0:run.book(row)
                else:run.trade(row)
        for run in runs:run.end_market(data['info']['last'])
        coverage.append(dict(close=close,first=info['first'],last=info['last'],tickers=[x['ticker'] for x in data['meta'].values()],raw_frames=info['raw_frames'],compact_rows=info['compact_rows']))
        if i%10==0:print(json.dumps(dict(i=i+1,elapsed=round(time.time()-start),primary_fills=sum(x['quantity']>0 for x in runs[2].ledger))),flush=True)
    out={}
    for run in runs:
        (P/('ledger_'+run.name+'.json')).write_text(json.dumps(run.ledger,separators=(',',':')))
        out[run.name]=dict(parameters=dict(target_cents=run.target/100,timeout_s=run.timeout/1000,stable=run.stable,latency_ms=run.L),all=metrics(run.ledger),by_asset={a:metrics([x for x in run.ledger if x['asset']==a]) for a in ('BTC','ETH')},by_period={p:metrics([x for x in run.ledger if (x['signal_ts']<SPLIT)==(p=='earlier')]) for p in ('earlier','later')},days={d:metrics([x for x in run.ledger if run.day(x['signal_ts'])==d]) for d in sorted({run.day(x['signal_ts']) for x in run.ledger})},candidate_counts=dict(run.buckets),risk_blocks=dict(run.rejections),final_cash=run.cash)
    (P/'results.json').write_text(json.dumps(dict(runs=out,coverage=coverage,elapsed=time.time()-start),indent=2));print(json.dumps({k:v['all'] for k,v in out.items()}),flush=True)
if __name__=='__main__':main()
