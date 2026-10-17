"""Two-sided quote experiment; historical inferred fills are explicitly unverified."""
from conditional import *

def fees(p,n):return math.ceil((.07*n*p*(1-p)-1e-12)*10000)/10000
def maker_describe(rows,lo=START,hi=END):
    x=describe(rows,lo,hi)
    # A two-leg basket has no single binary probability or break-even price.
    x.pop('mean_probability',None);x.pop('mean_breakeven',None)
    return x

def main():
    _,events=load();opps=[];cov=Counter()
    for event,e in events.items():
        t=e['close']-900
        if not START<=t<END:continue
        cov['events']+=1;choices=[]
        for m in e['items']:
            cs=e['cm'].get(m['ticker'],{});q=quote(cs.get(t))
            if q is None:continue
            mid=sum(q)/2
            if not .1<=mid<=.9 or q[1]-q[0]>.05000001:continue
            if sum(float(c.get('volume_fp') or 0) for tt,c in cs.items() if t-600<tt<=t)<5:continue
            choices.append((abs(mid-.5),m,cs,q))
        if not choices:cov['no_quoted_strike']+=1;continue
        _,m,cs,q=min(choices,key=lambda x:(x[0],x[1]['ticker']))
        mid=sum(q)/2;yes=math.floor((min(q[0],mid-.04)+1e-9)*100)/100
        no=math.floor((min(1-q[1],1-mid-.04)+1e-9)*100)/100
        if min(yes,no)<.01:cov['invalid_limit']+=1;continue
        c=cs.get(t+120);end=quote(c)
        try:low=float(c['price']['low_dollars']);high=float(c['price']['high_dollars']);vol=float(c['volume_fp'])
        except (KeyError,TypeError,ValueError):cov['no_subsequent_trade_candle']+=1;continue
        assert yes+no<=.9200001 and t+120<e['close']<e['settled']
        opps.append(dict(asset=e['asset'],event=event,ticker=m['ticker'],t=t,entered=t+120,close=e['close'],settled=e['settled'],
                         yes_limit=yes,no_limit=no,low=low,high=high,volume=vol,end_quote=end,yes_won=m['result']=='yes'))
    results={};ledgers={};attempt_audit={}
    for n in (5,1):
        for assumption in ('trade_through','adverse_end_quote'):
            for friction in (.01,.02):
                cash=100.;pending=[];active=[];ledger=[];daily=defaultdict(float);counts=Counter();submitted=[];mincash=100.;peakrisk=0.
                def advance(now):
                    nonlocal cash,pending,active
                    # Frozen-at-decision orders get a conservative execution timestamp.
                    for a in pending:
                        if a['entered']>now:continue
                        got=[]
                        for side,p in [('yes',a['yes_limit']),('no',a['no_limit'])]:
                            crossed=a['low']<=p-.01+1e-9 if side=='yes' else a['high']>=1-p+.01-1e-9
                            if assumption=='adverse_end_quote':
                                q=a['end_quote'];crossed=crossed and q is not None and sq(q,side)[1]<=p+1e-9
                            if crossed:got.append(side)
                        if a['volume']<n*len(got):got=[];counts['insufficient_total_volume']+=1
                        if not got:cash+=a['reserve'];counts['unfilled_pairs']+=1;continue
                        cost=sum(n*a[s+'_limit']+fees(a[s+'_limit'],n)+n*friction for s in got)
                        payout=n*sum(a['yes_won'] if s=='yes' else not a['yes_won'] for s in got)
                        b=dict(a,size=n,filled_sides=got,cost=cost,fee=sum(fees(a[s+'_limit'],n) for s in got),friction=friction,
                               payout=payout,net=payout-cost,won=payout>cost,model_p=.5,contracts=n*len(got))
                        cash+=a['reserve']-cost;active.append(b);ledger.append(b);counts['filled_legs']+=len(got);counts['both_sides_filled' if len(got)==2 else 'one_side_filled']+=1
                    pending=[a for a in pending if a['entered']>now]
                    for a in active:
                        if a['settled']<=now:cash+=a['payout'];daily[date(a['settled'])]+=a['net']
                    active=[a for a in active if a['settled']>now]
                for r in sorted(opps,key=lambda r:(r['t'],r['asset'])):
                    advance(r['t'])
                    reserve=n*(r['yes_limit']+r['no_limit'])+fees(r['yes_limit'],n)+fees(r['no_limit'],n)+2*n*friction
                    risk=sum(a['cost'] for a in active)+sum(a['reserve'] for a in pending)+reserve
                    contracts=sum(a['contracts'] for a in active)+sum(2*n for a in pending)+2*n
                    if contracts>25:counts['contract_limit']+=1;continue
                    if risk>25+1e-9 or daily[date(r['t'])]-risk < -20-1e-9:counts['risk_limit']+=1;continue
                    if cash-reserve<2:counts['cash_limit']+=1;continue
                    a=dict(r,reserve=reserve);cash-=reserve;pending.append(a);submitted.append(a);counts['submitted_pairs']+=1
                    mincash=min(mincash,cash);peakrisk=max(peakrisk,risk)
                advance(END+10*86400)
                assert not pending and not active and abs(cash-100-sum(a['net'] for a in ledger))<1e-7
                key=f'n{n}/{assumption}/cost{int(friction*100)}c';ledgers[key]=ledger;attempt_audit[key]=submitted
                results[key]={'all':maker_describe(ledger),'earlier':maker_describe([r for r in ledger if r['t']<SPLIT],START,SPLIT),
                    'later':maker_describe([r for r in ledger if r['t']>=SPLIT],SPLIT,END),
                    'cities':{c:metrics([r for r in ledger if r['asset']==c]) for c in CITIES},'counts':dict(counts),'min_cash':mincash,'peak_reserved':peakrisk,'end_cash':cash}
                print(key,len(ledger),results[key]['all']['net'],dict(counts),flush=True)
    save(P/'maker_opportunities.json.gz',opps);save(P/'maker_coverage.json',dict(cov));save(P/'maker_results.json',results);save(P/'maker_ledgers.json.gz',ledgers);save(P/'maker_attempts.json.gz',attempt_audit)

if __name__=='__main__':main()
