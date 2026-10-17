"""Public-data-only parallel research bot. All fills and balances are simulated.

No credentials, account endpoints, order transport, or real-money mode exists.
The two selected exploratory hypotheses are frozen in paper_config.json.
"""
import argparse,concurrent.futures,datetime as dt,hashlib,json,math,re,sqlite3,statistics,time
from pathlib import Path
import urllib.parse,urllib.request
from zoneinfo import ZoneInfo
from strategy import RULES,qualifies,tick_cap,book_fill,fee

P=Path(__file__).resolve().parent
STATE=P/'paper_state';BASE='https://api.elections.kalshi.com/trade-api/v2'
ASSETS=('BTC','ETH','SOL','XRP');ET=ZoneInfo('America/New_York')
LANES=('premium_z3','gaussian_edge8')
SELECTED=[next(r for r in RULES if r.name==n) for n in LANES]
START_CASH=50.;DAILY_BUDGET=5.;MAX_CONTRACTS=4;STRESS=.01

def get(url):
    parsed=urllib.parse.urlsplit(url)
    valid=(parsed.scheme=='https' and ((parsed.hostname=='api.elections.kalshi.com' and
        (parsed.path.startswith('/trade-api/v2/markets') or parsed.path.startswith('/trade-api/v2/series/')))
        or (parsed.hostname=='api.exchange.coinbase.com' and parsed.path.startswith('/products/'))))
    if not valid:raise ValueError('Only allowlisted public market-data GET endpoints are supported')
    req=urllib.request.Request(url,headers={'User-Agent':'kalshi-parallel-paper/1.0'},method='GET')
    with urllib.request.urlopen(req,timeout=8) as inp:return json.load(inp)

def timestamp(s):
    s=re.sub(r'\.(\d+)',lambda m:'.'+m.group(1)[:6].ljust(6,'0'),s)
    return int(dt.datetime.fromisoformat(s.replace('Z','+00:00')).timestamp())
def etday(t):return dt.datetime.fromtimestamp(t,ET).date().isoformat()

def feature(T,start,close,bid,ask,bars):
    """Only closes of completed minute bars may enter the signal."""
    rem=(close-T)/60
    if not 0<=bid<=ask<=1:raise ValueError('invalid quote')
    if not 3<=rem<=10:raise ValueError('outside window')
    keys=range(T-3660,T,60)
    if start-60 not in bars or not all(k in bars for k in keys):raise ValueError('incomplete spot')
    vals=[float(bars[k]) for k in keys]
    if min(vals)<=0 or not all(math.isfinite(x) for x in vals):raise ValueError('invalid spot')
    sigma=statistics.pstdev(math.log(y/x) for x,y in zip(vals,vals[1:]))
    if sigma<=0:raise ValueError('zero volatility')
    side=1 if bid+ask>1 else -1;b,a=(bid,ask) if side==1 else (1-ask,1-bid)
    ref=bars[start-60];z=side*(vals[-1]/ref-1)/(sigma*math.sqrt(rem))
    return dict(t=T,rem=rem,side=side,bid=b,ask=a,z=z,spot=vals[-1],ref=ref,sigma=sigma)

def scan(asset,T):
    rec=dict(asset=asset,t=T,mode='PAPER_ONLY',observed_ts=time.time())
    series='KX'+asset+'15M'
    mm=get(BASE+'/markets?'+urllib.parse.urlencode(dict(series_ticker=series,status='open',limit=20)))['markets']
    market=next((m for m in mm if timestamp(m['open_time'])<=T<timestamp(m['close_time'])),None)
    if not market:return dict(rec,status='no_current_market')
    start=timestamp(market['open_time']);close=timestamp(market['close_time']);rem=(close-T)/60
    rec.update(ticker=market['ticker'],close=close,remaining_minutes=rem)
    if not 3<=rem<=10:return dict(rec,status='outside_window')
    if close-start!=900 or float(market.get('notional_value_dollars',0))!=1:return dict(rec,status='unsupported_contract')
    q=urllib.parse.urlencode(dict(start_ts=T-60,end_ts=T,period_interval=1))
    cc=get(BASE+'/series/'+series+'/markets/'+market['ticker']+'/candlesticks?'+q).get('candlesticks',[])
    c=next((c for c in cc if c['end_period_ts']==T),None)
    if not c:return dict(rec,status='missing_decision_candle')
    try:b,a=float(c['yes_bid']['close_dollars']),float(c['yes_ask']['close_dollars'])
    except (KeyError,TypeError,ValueError):return dict(rec,status='missing_quote')
    rec['decision_quote']={'bid':b,'ask':a}
    q=urllib.parse.urlencode(dict(granularity=60,start=T-95*60,end=T))
    raw=get(f'https://api.exchange.coinbase.com/products/{asset}-USD/candles?'+q)
    bars={int(r[0]):float(r[4]) for r in raw if int(r[0])<=T-60}
    try:f=feature(T,start,close,b,a,bars)
    except ValueError as e:return dict(rec,status=str(e).replace(' ','_'))
    lanes=[r.name for r in SELECTED if qualifies(r,f)]
    rec.update(feature=f,lanes=lanes,price_ranges=market['price_ranges'],
        reference_current_qualifies=qualifies(RULES[0],f),source_bars=[[k,bars[k]] for k in range(T-3660,T,60)])
    if not 0<=time.time()-T<=25:return dict(rec,status='signal_too_old')
    return dict(rec,status='qualified' if lanes else 'signal_gate')

class Store:
    def __init__(self,path):
        path.parent.mkdir(parents=True,exist_ok=True)
        self.db=sqlite3.connect(path,timeout=10)
        self.db.execute('pragma journal_mode=WAL')
        self.db.execute('create table if not exists observations(id integer primary key,ts real,record text)')
        self.db.execute('create table if not exists entries(ticker text primary key,record text)')
        self.db.execute('create table if not exists metadata(key text primary key,value text)');self.db.commit()
    def observe(self,row):
        self.db.execute('insert into observations(ts,record) values(?,?)',(time.time(),json.dumps(row)));self.db.commit()
    def rows(self):return [json.loads(r[0]) for r in self.db.execute('select record from entries')]
    def enter(self,row):
        try:self.db.execute('insert into entries values(?,?)',(row['ticker'],json.dumps(row)));self.db.commit();return True
        except sqlite3.IntegrityError:self.db.rollback();return False
    def update(self,row):
        self.db.execute('update entries set record=? where ticker=?',(json.dumps(row),row['ticker']));self.db.commit()

def risk_reason(entries,asset,T):
    active=[r for r in entries if r['status']=='open'];settled=[r for r in entries if r['status']=='settled']
    if any(r['asset']==asset for r in active):return 'asset_exposure'
    if len(active)>=MAX_CONTRACTS:return 'contract_cap'
    realized=sum(r['pnl_after_stress'] for r in settled if r['settled_day']==etday(T))
    if realized-(len(active)+1)<-DAILY_BUDGET-1e-8:return 'daily_loss_budget'
    cash=START_CASH+sum(r['pnl_after_stress'] for r in settled)-sum(r['entry']+r['fee']+STRESS for r in active)
    if cash<1:return 'paper_cash'
    return ''

def observe_book(rec,store):
    T=rec['t'];now=time.time();entries=store.rows()
    if not 0<=now-T<=25:return dict(rec,status='signal_too_old')
    if any(r['ticker']==rec['ticker'] for r in entries):return dict(rec,status='already_entered')
    reason=risk_reason(entries,rec['asset'],now)
    if reason:return dict(rec,status=reason)
    raw=get(BASE+'/markets/'+rec['ticker']+'/orderbook?depth=100').get('orderbook_fp',{})
    observed=time.time()
    rec=dict(rec,book_ts=observed,book=raw)
    if observed-T>35:return dict(rec,status='book_too_late')
    book=(observed,tuple((float(p),float(n)) for p,n in raw.get('yes_dollars',[])),tuple((float(p),float(n)) for p,n in raw.get('no_dollars',[])))
    for name in rec['lanes']:
        rule=next(r for r in SELECTED if r.name==name);cap=tick_cap(rule,rec['feature'],rec['price_ranges'])
        if cap is None:continue
        filled=book_fill(book,rec['feature']['side'],cap,rule.lo,1)
        if not filled:continue
        price,paid=filled
        row=dict(ticker=rec['ticker'],asset=rec['asset'],lane=name,status='open',mode='PAPER_ONLY',
          entered_ts=observed,decision_ts=T,close=rec['close'],side='yes' if rec['feature']['side']==1 else 'no',
          entry=price,fee=paid,stress=STRESS,cap=cap,quantity=1,signal=rec['feature'],book_ts=observed,
          reference_current_qualifies=rec['reference_current_qualifies'])
        entered=store.enter(row)
        return dict(rec,status='simulated_entry' if entered else 'already_entered',lane=name,entry=price,fee=paid,cap=cap)
    return dict(rec,status='book_rejected')

def reconcile(store):
    errors=[]
    for row in store.rows():
        if row['status']!='open' or time.time()<row['close']+30:continue
        try:
            m=get(BASE+'/markets/'+row['ticker']).get('market',{})
            # The settled query filter returns markets whose response status is
            # finalized. Accept both terminal spellings, never a provisional result.
            if m.get('status') not in ('finalized','settled') or m.get('result') not in ('yes','no'):continue
            pnl=float(m['result']==row['side'])-row['entry']-row['fee']
            settled_ts=timestamp(m.get('settlement_ts') or m.get('settlement_time') or m['close_time'])
            store.update(dict(row,status='settled',result=m['result'],pnl_fee_only=pnl,pnl_after_stress=pnl-STRESS,
                reconciled_ts=time.time(),settled_ts=settled_ts,settled_day=etday(settled_ts)))
        except Exception as e:errors.append({'ticker':row['ticker'],'error':str(e)[:200]})
    return errors

def status(store,latest,errors,started):
    rr=store.rows();closed=[r for r in rr if r['status']=='settled'];active=[r for r in rr if r['status']=='open']
    result=dict(ts=time.time(),started_ts=started,mode='PAPER_ONLY',real_orders_enabled=False,
        lanes=LANES,contracts=1,max_contracts=MAX_CONTRACTS,daily_budget=DAILY_BUDGET,
        simulated_entries=len(rr),settled=len(closed),wins=sum(r['result']==r['side'] for r in closed),
        losses=sum(r['result']!=r['side'] for r in closed),net_after_stress=round(sum(r['pnl_after_stress'] for r in closed),4),
        net_fee_only=round(sum(r['pnl_fee_only'] for r in closed),4),open=active,latest=latest,errors=errors,
        by_lane={n:{'entries':sum(r['lane']==n for r in rr),'settled':sum(r['lane']==n for r in closed),
           'net_after_stress':round(sum(r['pnl_after_stress'] for r in closed if r['lane']==n),4)} for n in LANES},
        note='All entries are displayed-depth simulations. No orders are sent. Extra 1c cost per contract included.')
    tmp=STATE/'status.tmp';tmp.write_text(json.dumps(result,indent=2));tmp.replace(STATE/'status.json')
    return result

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--once',action='store_true');args=parser.parse_args()
    config=json.loads((P/'paper_config.json').read_text())
    for name,expected in config['source_hashes'].items():
        assert hashlib.sha256((P/name).read_bytes()).hexdigest()==expected,'Frozen paper source changed: '+name
    store=Store(STATE/'paper.sqlite3');started=time.time();latest=[];errors=[];pending=[];last=None;last_reconcile=0
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        while True:
            now=time.time();T=int(now//60)*60
            if now-last_reconcile>=10:
                errors=reconcile(store);last_reconcile=time.time()
            if T!=last and now>=T+2:
                pending=[];latest=[]
                jobs={pool.submit(scan,a,T):a for a in ASSETS}
                for job in concurrent.futures.as_completed(jobs):
                    try:rec=job.result()
                    except Exception as e:rec={'asset':jobs[job],'t':T,'mode':'PAPER_ONLY','status':'data_error','error':str(e)[:200]}
                    store.observe(rec);latest.append({k:v for k,v in rec.items() if k not in ('source_bars','price_ranges')})
                    if rec['status']=='qualified':pending.append(dict(rec,checks=0))
                last=T
            left=[]
            for rec in pending:
                if rec['checks']>=8 or time.time()-rec['t']>25:continue
                try:result=observe_book(rec,store)
                except Exception as e:result=dict(rec,status='book_error',error=str(e)[:200])
                store.observe(result)
                latest=[r for r in latest if r['asset']!=rec['asset']]+[{k:v for k,v in result.items() if k not in ('source_bars','price_ranges','book')}]
                if result['status'] in ('book_rejected','book_error'):left.append(dict(rec,checks=rec['checks']+1))
            pending=left
            summary=status(store,latest,errors,started)
            if args.once:
                print(json.dumps({k:v for k,v in summary.items() if k not in ('latest','open')}));break
            time.sleep(3 if pending else 1)

if __name__=='__main__':main()
