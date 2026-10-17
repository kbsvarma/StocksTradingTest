"""Frozen five-minute empirical transfer, no order/client dependencies."""
from core import *
from decimal import ROUND_FLOOR
import argparse,bisect,pickle,math
PERIODS={'earlier':(ts('2026-09-14T04:00:00Z'),ts('2026-10-03T04:00:00Z')),
         'later':(ts('2026-10-03T04:00:00Z'),ts('2026-10-12T04:00:00Z')),
         'continuous':(ts('2026-09-14T04:00:00Z'),ts('2026-10-12T04:00:00Z'))}
GROUPS={'nonmetal':('WTI','NATGAS','EURUSD','GBPUSD','USDJPY','AUDUSD','USDCAD'),
        'new_all':('WTI','NATGAS','EURUSD','GBPUSD','USDJPY','AUDUSD','USDCAD','PLATINUM','PALLADIUM'),
        'metals_control':('GOLD','SILVER','COPPER')}
def limit_price(ask,side,ranges):
    spend=min(D(str(ask))+D('.01'),D('.939'));yes=spend if side==1 else 1-spend
    for row in ranges:
        lo,hi,step=map(D,(row['start'],row['end'],row['step']))
        if lo<=yes<=hi:
            px=lo+((yes-lo)/step).to_integral_value(rounding=ROUND_FLOOR if side==1 else ROUND_CEILING)*step
            return float(px if side==1 else 1-px)
    raise ValueError('uncovered tick')
def prepare():
    rows=[];coverage=C.defaultdict(C.Counter);source=[]
    paths=[P/'financial_inputs/features.pickle',ROOT/'kalshi_currency_review_2026_10_12/features.pickle',ROOT/'kalshi_metals_research_2026_10_11/features.pickle']
    seen=set()
    for p in paths:
        source.append(dict(path=str(p),sha256=hashlib.sha256(p.read_bytes()).hexdigest()))
        for m in pickle.load(open(p,'rb')):
            assert m['ticker'] not in seen;seen.add(m['ticker']);q=coverage[m['asset']];q['markets']+=1
            q['first_close']=min(q.get('first_close',m['close']),m['close']);q['last_close']=max(q.get('last_close',m['close']),m['close'])
            fs=[f for f in m['features'] if f['rem']==5]
            if len(fs)!=1:q['missing_five_minute_feature']+=1;continue
            f=fs[0]
            if m['issues']:q['source_integrity_excluded']+=1;continue
            assert m['result'] in (0,1) and m['settled']>=m['close']>f['t']
            assert f['t']==m['close']-300 and math.isfinite(f['residual']+f['threshold'])
            r=dict(m);r['f']=f;del r['features'];rows.append(r);q['usable_five_minute']+=1
    audit=[]
    for a in sorted(coverage):
        ar=[r for r in rows if r['asset']==a];weeks=sorted({(r['f']['t']-345600)//604800*604800+345600 for r in ar})
        for week in weeks:
            tr=[r for r in ar if r['settled']<week]
            te=[r for r in ar if week<=r['f']['t']<week+604800]
            if len(tr)<250:coverage[a]['insufficient_training']+=len(te);continue
            rr=sorted(r['f']['residual'] for r in tr)
            for r in te:
                f=r['f'];p=(len(rr)-bisect.bisect_left(rr,f['threshold'])+1)/(len(rr)+2)
                r['prob']=.8*p+.2*(f['ybid']+f['yask'])/2
            audit.append(dict(asset=a,week=week,n=len(tr),last_settlement=max(r['settled'] for r in tr),test=len(te)))
    (P/'financial_features.pickle').write_bytes(pickle.dumps(rows))
    save('financial_coverage.json',coverage);save('financial_sources.json',source);save('financial_model_audit.json',audit)
    print('FINANCIAL FEATURES',len(rows),'models',len(audit),flush=True)
def candidates(rows,floor,qty,mode):
    out=[];diag=C.Counter()
    for r in rows:
        if 'prob' not in r:continue
        f=r['f'];p=r['prob'];t=f['t'];yes=(1,f['yask'],p);no=(-1,1-f['ybid'],1-p)
        side,ask,pr=max((yes,no),key=lambda z:z[2]-z[1]-fee(z[1],qty,r['multiplier'])/qty-.01)
        if not floor-1e-8<=ask<.95-1e-8 or f['yask']-f['ybid']>.020000001:continue
        if pr-ask-fee(ask,qty,r['multiplier'])/qty-.01<.02:continue
        diag['qualified']+=1;cap=limit_price(ask,side,r['ranges']);q=quote(r['candles'].get(t+60),'close' if mode=='delay60' else 'open',side)
        if ask>cap+1e-8:diag['signal_ask_above_limit']+=1;continue
        if mode=='all_cap':entry=cap
        else:
            if q is None:diag['missing_execution_quote']+=1;continue
            if not floor-1e-8<=q[1]<=cap+1e-8:diag['next_ask_outside_limits']+=1;continue
            entry=cap if mode=='cap' else max(ask,q[1])
        out.append(dict(owner=r['ticker'],asset=r['asset'],ticker=r['ticker'],side=side,t=t,entry_t=t+(60 if mode=='delay60' else 3),
                        entry=round(entry,8),limit=cap,exit_t=r['settled'],exit_price=int(r['result']==int(side==1)),
                        exit_kind='settlement',mult=r['multiplier'],prob=pr,signal_ask=ask))
    return out,dict(diag)
def run(mode):
    rows=pickle.load(open(P/'financial_features.pickle','rb'));result={};logs={}
    periods=['earlier'] if mode=='select' else ['later','continuous']
    for floor in (.5,.1):
        for qty in (1,5):
            name=f'floor{floor}_q{qty}';result[name]={};logs[name]={}
            for scenario,book,cost in [('base','base',.01),('cost2','base',.02),('cost3','base',.03),('cap','cap',.01),('all_cap','all_cap',.01),('delay60','delay60',.01),('delay60_cost2','delay60',.02)]:
                pool,diag=candidates(rows,floor,qty,book);result[name][scenario]={};logs[name][scenario]={}
                for group,assets in GROUPS.items():
                    result[name][scenario][group]={};logs[name][scenario][group]={}
                    for period in periods:
                        lo,hi=PERIODS[period];eligible=[r for r in pool if r['asset'] in assets and lo<=r['t']<hi]
                        ledger,account=portfolio(eligible,qty,cost);m=metrics(ledger,(hi-lo)/86400);m.update(account)
                        m.update(qualified_proxy_candidates=len(eligible),candidate_diagnostics_all_assets=diag)
                        result[name][scenario][group][period]=m;logs[name][scenario][group][period]=ledger
            print(mode,name,{g:{p:{k:result[name]['base'][g][p][k] for k in ('trades','net','trades_per_day')} for p in periods} for g in GROUPS},flush=True)
    save('financial_'+mode+'_results.json',result);save('financial_'+mode+'_ledgers.json.gz',logs)
    if mode=='select':
        passing=[]
        for n in result:
            for g in ('nonmetal','new_all'):
                x=result[n]
                if x['base'][g]['earlier']['trades_per_day']>=10 and all(x[s][g]['earlier']['net']>0 for s in ('base','cost2','all_cap','delay60_cost2')):passing.append([n,g])
        save('financial_selection_frozen.json',dict(at=dt.datetime.now(dt.timezone.utc).isoformat(),passing=passing,later_not_evaluated=True,source_sha=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()))
    else:assert (P/'financial_selection_frozen.json').exists()
if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('mode',choices=['prepare','select','evaluate']);args=parser.parse_args()
    prepare() if args.mode=='prepare' else run(args.mode)
