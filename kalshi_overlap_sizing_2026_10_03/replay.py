"""Offline overlap experiments. No account, order, service, or network transport."""
import bisect, collections, datetime as dt, hashlib, json, math, pickle, sys
from pathlib import Path
P=Path(__file__).resolve().parent;ROOT=P.parent
sys.path.insert(0,str(ROOT/'kalshi_z_time_research_2026_10_03'))
import study as s
s.old.FOLDERS=s.FOLDERS

POLICIES=('baseline','overlap7','overlap10','strict7','strict10','parallel5')
def save(name,x): (P/name).write_text(json.dumps(x,indent=2,allow_nan=False))
def intersection(r): return .93-1e-8<=r['ask']<.95-1e-8 and r['z']>=3 and 3<=r['rem']<=10

def range_fill(book,side,cap,floor,n,skip=0):
    """Consume any already-used cheapest quantity before filling the new order."""
    remaining=float(n);cost=raw=0.
    for bid,q in sorted(book[2] if side==1 else book[1],reverse=True):
        price=1-bid
        if q<=0 or not 0<=price<=1:continue
        if price<floor-1e-8:return None
        if price>cap+1e-8:break
        used=min(skip,q);skip-=used;q-=used
        take=min(q,remaining);remaining-=take;cost+=take*price;raw+=.07*take*price*(1-price)
        if remaining<=1e-8:
            debit=math.ceil((cost+raw-1e-10)*10000)/10000
            return cost/n,debit-cost
    return None

def events(name,markets,books=None):
    records,a=pickle.loads((s.P/(name+'_opportunities.pickle')).read_bytes())
    lookup={m['ticker']:m for m in markets};out=[];depth=collections.Counter()
    for ar in a[a[:,s.LANE]<2]:
        r=dict(records[int(ar[s.OID])],lane=int(ar[s.LANE]));m=lookup[r['ticker']]
        r['overlap']=intersection(r);r['fills']={5:(r['entry'],r['fee'])};r['strict']={}
        r['original_cap']=s.tick_cap(s.PROFILES[0],r,m['ranges'])
        if books is None:
            for n in (7,10):r['fills'][n]=(r['entry'],s.fee(r['entry'],n))
            r['second_fill']=r['fills'][5]
            for n in (7,10):r['strict'][n]=(.93-1e-8<=r['entry']<=r['original_cap']+1e-8)
        else:
            bb=books[r['ticker']];i=bisect.bisect_left([b[0] for b in bb],r['entered'])
            assert bb[i][0]==r['entered'];book=bb[i]
            check=range_fill(book,r['side'],r['cap'],s.PROFILES[r['profile']].lo,5)
            assert check and max(abs(check[j]-r['fills'][5][j]) for j in (0,1))<1e-8
            for n in (7,10):
                r['fills'][n]=range_fill(book,r['side'],r['cap'],s.PROFILES[r['profile']].lo,n)
                r['strict'][n]=range_fill(book,r['side'],r['original_cap'],.93,n) is not None
                if r['overlap']:depth[f'overlap_{n}_supported' if r['fills'][n] else f'overlap_{n}_insufficient']+=1
            r['second_fill']=range_fill(book,r['side'],r['cap'],s.PROFILES[r['profile']].lo,5,skip=5)
        out.append(r)
    return out,records,a,dict(depth)

def replay(ev,policy='baseline',stress=.01,capcost=False):
    seen={};active=[];accepted=[];daily=collections.defaultdict(float);own=collections.defaultdict(float)
    cash=50.;counts=collections.Counter();maxima=collections.defaultdict(float)
    for e in ev:
        still=[]
        for p in active:
            if p['settled']<=e['entered']:
                cash+=p['quantity']*p['won'];day=s.etday(p['settled']);daily[day]+=p['net']
                if p['lane']==1:own[day]+=p['net']
            else:still.append(p)
        active=still;prior=seen.get(e['ticker'],[]);second=bool(prior)
        if second:
            if policy!='parallel5' or len(prior)>=2 or any(p['lane']==e['lane'] for p in prior):continue
            if prior[0]['side']!=e['side'] or not any(p['ticker']==e['ticker'] for p in active):continue
        if any(p['asset']==e['asset'] and p['ticker']!=e['ticker'] for p in active):continue
        if policy!='parallel5' and any(p['asset']==e['asset'] for p in active):continue
        count=sum(p['quantity'] for p in active);risk=sum(p['reserve'] for p in active)
        owncount=sum(p['quantity'] for p in active if p['lane']==1);day=s.etday(e['entered'])
        target=int(policy[-2:] if policy.endswith('10') else policy[-1]) if policy.startswith(('overlap','strict')) and e['overlap'] else 5
        chosen=None
        for n in ([target,5] if target!=5 else [5]):
            fill=e['fills'].get(n)
            if second and prior[0]['entered']==e['entered']:
                fill=e['second_fill']
            if fill is None:counts['insufficient_depth_'+str(n)]+=1;continue
            if n!=5 and policy.startswith('strict') and not e['strict'][n]:counts['strict_price_fallback']+=1;continue
            reserve=n*(.95 if e['profile']==0 else 1.)
            if count+n>20 or risk+reserve>19+1e-8:counts['capacity_block_'+str(n)]+=1;continue
            if daily[day]-risk-reserve < -10-1e-8:counts['daily_block_'+str(n)]+=1;continue
            if e['lane']==1 and (owncount+n>20 or own[day]-owncount-n < -25-1e-8):continue
            if cash<max(5.,reserve)-1e-8:counts['cash_block_'+str(n)]+=1;continue
            chosen=(n,fill,reserve);break
        if chosen is None:continue
        n,fill,reserve=chosen;price,paid=fill
        if capcost:price=e['cap'];paid=s.fee(price,n)
        debit=n*price+paid+stress*n;cash-=debit
        row={k:v for k,v in e.items() if k not in ('fills','strict','second_fill')}
        row.update(quantity=n,entry=price,fee=paid,reserve=reserve,pnl=n*(e['won']-price)-paid,pnl_cap=n*(e['won']-e['cap'])-s.fee(e['cap'],n),net=n*e['won']-debit,second=second,second_later=second and e['t']>prior[0]['t'])
        active.append(row);seen.setdefault(e['ticker'],[]).append(row);accepted.append(row)
        maxima['contracts']=max(maxima['contracts'],count+n);maxima['risk']=max(maxima['risk'],risk+reserve)
        maxima['daily_committed_loss']=max(maxima['daily_committed_loss'],risk+reserve-daily[day])
        assert count+n<=20 and risk+reserve<=19+1e-8 and risk+reserve-daily[day]<=10+1e-8
        assert cash>=-1e-8 and sum(p['quantity'] for p in seen[e['ticker']])<=10
    return accepted,dict(counts),dict(maxima)

def metrics(rows,stress=.01):
    out=s.old.metrics(rows,stress)
    out.update(markets=len(set(r['ticker'] for r in rows)),contracts=sum(r['quantity'] for r in rows),boosted=sum(r['quantity']>5 for r in rows),second_legs=sum(r['second'] for r in rows),later_second_legs=sum(r['second_later'] for r in rows))
    # Market-level wins/losses and drawdown after actual settlement times.
    market={r['ticker']:r['won'] for r in rows};out.update(market_wins=sum(market.values()),market_losses=len(market)-sum(market.values()))
    eq=high=dd=0.;buckets=collections.defaultdict(float)
    for r in rows:buckets[r['settled']]+=r['pnl']-stress*r['quantity']
    for t in sorted(buckets):eq+=buckets[t];high=max(high,eq);dd=max(dd,high-eq)
    out['settled_drawdown']=round(dd,5)
    return out

def grouped(rows,stress):
    return {name:metrics([r for r in rows if pred(r)],stress) for name,pred in {
        'simultaneous_at_first_entry':lambda r:r['overlap'] and not r['second'],
        'single_lane_at_first_entry':lambda r:not r['overlap'] and not r['second'],
        'boosted':lambda r:r['quantity']>5,
        'second_leg':lambda r:r['second'],
        'later_second_leg':lambda r:r['second_later']}.items()}

def run():
    markets=pickle.loads((s.P/'features.pickle').read_bytes());books=s.old.load_books()
    allout={};checks=[];alltrades={};depth={}
    for name in ('quote','books25','books25_delay10'):
        ev,records,a,dc=events(name,markets,books if name!='quote' else None);depth[name]=dc;allout[name]={};alltrades[name]={}
        for period in ('all',)+s.PERIODS:
            if period=='all':ee=ev;aa=a
            else:
                i=s.PERIODS.index(period);ee=[e for e in ev if s.BOUNDS[i]<=e['close']<s.BOUNDS[i+1]];aa=s.subset(a,period)
            if not ee:continue
            allout[name][period]={};alltrades[name][period]={}
            for scenario,stress,capcost in [('primary',.01,False),('two_cent',.02,False),('cap_cost',.01,True)]:
                results={};rowsby={}
                for policy in POLICIES:
                    rows,counts,maxima=replay(ee,policy,stress,capcost);rowsby[policy]=rows
                    results[policy]=dict(metrics=metrics(rows,stress),groups=grouped(rows,stress),counts=counts,maxima=maxima)
                expected=s.selected_rows(records,aa,stress=stress,capcost=capcost)
                got=rowsby['baseline']
                identity=lambda rr:sorted((r['ticker'],r['entered'],r['entry'],round(r['fee'],8)) for r in rr)
                assert identity(expected)==identity(got),(name,period,scenario)
                assert abs(s.old.metrics(expected,stress).get('net',0)-metrics(got,stress).get('net',0))<1e-6
                checks.append([name,period,scenario,len(got)])
                base=results['baseline']['metrics'];baseids={r['ticker'] for r in got}
                # Paired differences by market-close day, accounting for displaced trades.
                for policy,rows in rowsby.items():
                    mm=results[policy]['metrics'];ids={r['ticker'] for r in rows}
                    results[policy]['delta']={'net':round(mm.get('net',0)-base.get('net',0),5),'markets':mm['markets']-base['markets'],'new_markets':len(ids-baseids),'displaced_markets':len(baseids-ids)}
                    daily=collections.defaultdict(float)
                    for sign,rr in [(1,rows),(-1,got)]:
                        for r in rr:daily[r['day']]+=sign*(r['pnl']-stress*r['quantity'])
                    results[policy]['daily_delta']=dict(sorted(daily.items()))
                allout[name][period][scenario]=results
                if scenario=='primary':alltrades[name][period]=rowsby
            print(name,period,{p:(x['metrics']['markets'],x['metrics']['net'],x['delta']['net']) for p,x in allout[name][period]['primary'].items()},flush=True)
    save('results.json',allout);save('depth.json',depth);save('validation.json',dict(baseline_parity=checks,plan_sha256=hashlib.sha256((P/'PLAN.md').read_bytes()).hexdigest()))
    (P/'trades.pickle').write_bytes(pickle.dumps(alltrades,protocol=5))
    save('quote_later_trades.json',alltrades['quote']['later'])

if __name__=='__main__':run()
