"""Posthoc passive-first sensitivity. Trade-through proxy, NOT verified fills."""
from core import *
from recurring import choices,execute,vol
from concurrent.futures import ThreadPoolExecutor

def inputs():
    dest=P/'daily_extension';index=read(dest/'daily_index.json');out=[]
    for g in index:
        t=g['times'][1 if g['family']=='gas' else 0];path=dest/'daily_quotes'/(str(t)+'.json.gz');cm={k:{c['end_period_ts']:c for c in v} for k,v in read(path).items()} if path.exists() else {};probs={}
        for m in g['markets']:
            if ts(m['open_time'])<=t<ts(m['close_time']):
                q=qte(cm.get(m['ticker'],{}).get(t))
                if q:probs[m['ticker']]=sum(q)/2
        opts=choices(g['markets'],cm,t,g['asset'],g['owner'],'passive_proxy',g['mult'],probs,{'date':g['date'],'family':g['family']})
        yes=[x for x in opts if x['side']=='yes']
        if not yes:continue
        if g['family']=='temperature':pick=max(yes,key=lambda x:(x['mid'],x['ticker']))
        else:
            atm=min(yes,key=lambda x:(abs(x['mid']-.5),x['ticker']));pick=max([x for x in opts if x['ticker']==atm['ticker']],key=lambda x:(x['mid'],x['side']))
        if not .01<=pick['ask']<=.99 or pick['ask']-pick['bid']>.05000001 or vol(pick['_cs'],t)<5:continue
        out.append((g,t,pick))
    return index,out

def collect():
    _,items=inputs();cut=ts(get('/historical/cutoff')['market_settled_ts'])
    def fetch(item):
        g,t,x=item;m=x['_m'];path=P/'passive_quotes'/(m['ticker']+'.json.gz')
        if not path.exists():
            endpoint='/historical/markets/'+m['ticker']+'/candlesticks' if ts(m['settlement_ts'])<cut else '/series/'+g['asset']+'/markets/'+m['ticker']+'/candlesticks'
            save(path,get(endpoint,{'start_ts':t,'end_ts':t+360,'period_interval':1}))
    print('PASSIVE_FETCH',len(items),flush=True)
    with ThreadPoolExecutor(max_workers=4) as pool:
        for i,_ in enumerate(pool.map(fetch,items),1):
            if i%100==0:print('PASSIVE_QUOTES',i,flush=True)

def trade_extreme(c,side):
    phase='low' if side=='yes' else 'high';obj=c.get('price',{});v=obj.get(phase+'_dollars',obj.get(phase))
    try:p=float(v)/100 if isinstance(v,int) else float(v)
    except (TypeError,ValueError):return None
    return round(p if side=='yes' else 1-p,4) if 0<=p<=1 else None

def main():
    index,items=inputs();cand=defaultdict(list);skips=defaultdict(Counter)
    for g,t,x in items:
        cs={c['end_period_ts']:c for c in read(P/'passive_quotes'/(x['ticker']+'.json.gz')).get('candlesticks',[])}
        limit=round(math.floor((x['ask']-.01+1e-10)*100)/100,2)
        for fallback in [False,True]:
            for allowance in [1,2]:
                key=f"{'fallback' if fallback else 'passive_only'}/through{allowance}c";r=None
                # Order is assumed submitted after the decision; ignore first
                # minute so prints before arrival cannot fill the order.
                # A strict trade-through is stronger than a mere price touch,
                # but still cannot establish queue, acknowledgement or depth.
                for end in range(t+120,t+301,60):
                    c=cs.get(end,{});price=trade_extreme(c,x['side']);quantity=float(c.get('volume_fp') or c.get('volume') or 0)
                    if limit>=.01 and price is not None and price<=limit-allowance*.01+1e-8 and quantity>=5:
                        r={**{k:v for k,v in x.items() if not k.startswith('_')},'t':t,'entered':end,'entry':limit,'cap':limit,'settled':ts(x['_m']['settlement_ts']),'payout':pay(x['_m'],x['side']),'volume10':vol(x['_cs'],t),'mult':0,'original_mult':x['mult'],'fill_type':'unverified_strict_trade_through','through_price':price,'maker_limit':limit,'through_cents':allowance};break
                if r is None and fallback:
                    # Same chosen side, fixed5minute fallback and original+2c cap.
                    q=qte(cs.get(t+300),x['side']);q2=qte(cs.get(t+360),x['side'],'open');cap=min(.99,x['ask']+.02)
                    if q and q2 and .01<=q[1]<=.99 and q[1]-q[0]<=.05000001 and max(q[1],q2[1])<=cap+1e-8:
                        r={**{k:v for k,v in x.items() if not k.startswith('_')},'t':t,'entered':t+303,'entry':max(q[1],q2[1]),'cap':cap,'settled':ts(x['_m']['settlement_ts']),'payout':pay(x['_m'],x['side']),'volume10':vol(x['_cs'],t),'fill_type':'taker_fallback_proxy','original_mult':x['mult']}
                if r and r['entered']<min(r['close'],r['settled']):cand[key].append(r)
                else:skips[key]['no_proxy_fill_or_cap']+=1
    result={};ledgers={}
    for key,rows in cand.items():
        for asset in ['ALL','KXAAAGASD','KXHIGHNY']:
            for part in ['all','older','recent','october']:
                choose=lambda d:part=='all' or (d<'2026-08-15' if part=='older' else d>='2026-08-15' if part=='recent' else d>='2026-10-01');ww=[w for w in index if (asset=='ALL' or w['asset']==asset) and choose(w['date'])];rr=[r for r in rows if (asset=='ALL' or r['asset']==asset) and choose(r['date'])]
                for q in [1,5]:
                    for cost in [.01,.02]:
                        k=asset+'/'+key+'/'+part+f'/q{q}/cost{round(cost*100)}';record(result,ledgers,k,rr,len(ww),q,cost)
                        result[k]['unverified_passive_fills']=sum(r['fill_type'].startswith('unverified') for r in ledgers[k])
    save(P/'passive_results.json',result);save(P/'passive_ledgers.json.gz',ledgers);save(P/'passive_skips.json',{k:dict(v) for k,v in skips.items()})
    for k,v in result.items():
        if k.startswith('ALL/') and k.endswith('/q1/cost1'):print(k,{a:v[a] for a in ['windows','trades','wins','losses','net','drawdown','unverified_passive_fills']})
if __name__=='__main__':
    import sys
    collect() if 'collect' in sys.argv else main()
