"""Frozen exact two-lane crypto input replay; independent of live services."""
import collections as C,datetime as dt,gzip,importlib.util,json,math,pickle,statistics,sys
from decimal import Decimal as D,ROUND_CEILING
from pathlib import Path
import pull as p
P=p.P;OLD=P.parent/'kalshi_market_expansion_2026_10_09'
spec=importlib.util.spec_from_file_location('archived_replay',OLD/'extension/replay.py')
r=importlib.util.module_from_spec(spec);spec.loader.exec_module(r)
ASSETS=('DOGE','HYPE','NEAR','ZEC','BNB')
PRIOR_END=p.ts('2026-10-09T15:00:00Z')
def save(n,x):p.write(P/n,x)
def build(data,end):
    markets=[];coverage={};revisions=[]
    for a in ASSETS:
        q=C.Counter();prices={}
        for batch in map(json.loads,(data/f'spot_{a}.jsonl').read_text().splitlines()):
            for row in batch['rows']:
                t,v=int(row[0]),float(row[4]);assert t%60==0 and v>0 and math.isfinite(v)
                if t in prices and prices[t]!=v:revisions.append(dict(asset=a,t=t,old=prices[t],new=v))
                prices[t]=v
        quotes={b['ticker']:{int(x['t']):x for x in b['candles']} for b in map(json.loads,(data/f'candles_{a}.jsonl').read_text().splitlines())}
        series=json.loads((data/f'series_{a}.json').read_text())['response']['series'];assert series['fee_type']=='quadratic'
        for raw in json.loads((data/f'markets_{a}.json').read_text()):
            start,close=p.ts(raw['open_time']),p.ts(raw['close_time'])
            if not p.START<=close<end:continue
            assert close-start==900 and raw['result'] in ('yes','no') and float(raw['notional_value_dollars'])==1
            m=dict(ticker=raw['ticker'],asset=a,start=start,close=close,settled=p.ts(raw['settlement_ts']),result=1 if raw['result']=='yes' else -1,
                ranges=raw['price_ranges'],candles=quotes.get(raw['ticker'],{}),multiplier=float(series['fee_multiplier']),
                features=[],quote_features=[],volume=float(raw['volume_fp']))
            assert m['settled']>=close
            q['markets']+=1
            for rem in range(10,2,-1):
                T=close-rem*60;pair=r.quote(m['candles'].get(T))
                if pair is None:q['missing_quote']+=1;continue
                side=1 if sum(pair)>1 else -1;bid,ask=pair if side==1 else (1-pair[1],1-pair[0])
                f=dict(t=T,rem=rem,side=side,bid=bid,ask=ask);m['quote_features'].append(f.copy())
                priced=any(r.qualifies(dict(f,z=10),l) for l in (r.Z2,r.Z3))
                if priced:q['price_spread_time_qualified_minutes']+=1
                extra=r.feature(prices,start,T,rem,side)
                if extra is None:
                    q['incomplete_or_zero_volatility']+=1
                    if priced:q['price_qualified_spot_gap']+=1
                    continue
                f.update(extra);m['features'].append(f);q['feature_minutes']+=1
                if priced:q['price_qualified_complete_source']+=1
                if any(r.qualifies(f,l) for l in (r.Z2,r.Z3)):q['z_qualified_minutes']+=1
            markets.append(m)
        q['missing_spot_minutes']=sum(t not in prices for t in range(p.START,end,60));q['expected_spot_minutes']=(end-p.START)//60
        coverage[a]=dict(q)
    return markets,coverage,revisions
def stress_pool(markets):
    out=[]
    for m in markets:
        for f in m['features']:
            for lane in (r.Z2,r.Z3):
                if not r.qualifies(f,lane):continue
                _,cap=r.limit_price(dict(lane=lane,signal_ask=f['ask'],side='yes' if f['side']==1 else 'no',price_ranges=m['ranges']))
                cap=float(cap);fee=r.fees(cap,5,multiplier=m['multiplier']);won=int(f['side']==m['result'])
                out.append(dict(ticker=m['ticker'],asset=m['asset'],lane=lane,t=f['t'],entered=f['t']+3,close=m['close'],settled=m['settled'],
                    side=f['side'],entry=cap,cap=cap,quantity=5,fee=fee,won=won,pnl=5*(won-cap)-fee,ask=f['ask'],bid=f['bid'],rem=f['rem'],z=f['z'],mode='all_cap'))
    return out
def stats(xs):
    xs=sorted(xs);n=len(xs)
    return dict(windows=n,total=sum(xs),mean=statistics.mean(xs),median=statistics.median(xs),minimum=min(xs),zero=sum(x==0 for x in xs)) if xs else dict(windows=0)
old,cov,_=build(OLD/'crypto/data',PRIOR_END)
expected=json.loads((OLD/'crypto_results.json').read_text());controls=[]
scenarios=(('plus1c','opening',.01),('plus2c','opening',.02),('cap_plus1c','cap_cost',.01),('delay60_plus1c','delay60',.01))
for asset in ASSETS:
    for label,mode,friction in scenarios:
        pool,_=r.opportunities([m for m in old if m['asset']==asset],mode)
        ledger,_=r.portfolio(pool,friction);got=r.metrics(ledger);want=expected[asset+'/standard'][label]['periods']['full']
        checks={k:abs(got[k]-want[k])<1e-6 for k in ('trades','wins','losses','net','settled_drawdown')};assert all(checks.values()),(asset,label,got,want)
        controls.append(dict(asset=asset,scenario=label,checks=checks))
save('control_reproduction.json',dict(passed=True,controls=controls))
markets,coverage,revisions=build(p.DATA,p.END)
save('coverage.json',coverage);save('source_revisions.json',revisions)
(P/'features.pickle').write_bytes(pickle.dumps(markets))
oldf={(m['ticker'],f['t']):f for m in old for f in m['features']};nf={(m['ticker'],f['t']):f for m in markets for f in m['features']}
parity=[]
for key,f in oldf.items():
    n=nf.get(key)
    if n is None or any(abs(n[k]-f[k])>1e-9 for k in ('ref','spot','z','bid','ask')):parity.append(dict(key=key,old=f,new=n))
save('prior_feature_changes.json',dict(checked=len(oldf),differences=parity))
results={};ledgers={};validation=0
for asset in ASSETS:
    ms=[m for m in markets if m['asset']==asset];results[asset]={};ledgers[asset]={}
    for label,mode,friction in (*scenarios,('allcap_plus1c','all_cap',.01),('allcap_plus2c','all_cap',.02)):
        pool,counts=(stress_pool(ms),{}) if mode=='all_cap' else r.opportunities(ms,mode)
        periods={};ledgers[asset][label]={}
        for name,lo,hi in [('full',p.START,p.END),('prior',p.START,PRIOR_END),('extension',PRIOR_END,p.END)]:
            ledger,blocks=r.portfolio([x for x in pool if lo<=x['close']<hi],friction,start_cash=100)
            m=r.metrics(ledger);m['blocked']=blocks;m['lanes']={l:r.metrics([x for x in ledger if x['lane']==l]) for l in (r.Z2,r.Z3)}
            periods[name]=m;ledgers[asset][label][name]=ledger
            assert len(ledger)==len({x['ticker'] for x in ledger})
            for x in ledger:
                assert x['t']<x['entered']<x['close']<=x['settled'] and x['quantity']==5
                assert float(r.LANES[x['lane']].floor)-1e-8<=x['entry']<=x['cap']+1e-8
                market=next(y for y in ms if y['ticker']==x['ticker']);assert x['won']==int(x['side']==market['result'])
                price=D(str(x['entry']));cost=5*price
                fee=(cost+(D('.07')*D(str(market['multiplier']))*5*price*(1-price)).quantize(D('.000001'),rounding=ROUND_CEILING)).quantize(D('.0001'),rounding=ROUND_CEILING)-cost
                assert abs(float(fee)-x['fee'])<1e-9
                assert abs(float(5*(D(x['won'])-price)-fee-D(str(friction))*5)-x['net'])<1e-8
                validation+=1
        results[asset][label]=dict(periods=periods,counts=counts)
    results[asset]['volume_full']=stats([m['volume'] for m in ms]);results[asset]['volume_last24h']=stats([m['volume'] for m in ms if p.END-86400<=m['close']<p.END])
    results[asset]['trades_per_calendar_day']=results[asset]['plus1c']['periods']['full']['trades']/((p.END-p.START)/86400)
    print(asset,{label:{per:{k:v[k] for k in ('trades','wins','losses','net','settled_drawdown')} for per,v in results[asset][label]['periods'].items() if per!='prior'} for label in ('plus1c','plus2c','allcap_plus1c')},flush=True)
save('results.json',results)
with gzip.open(P/'ledgers.json.gz','wt') as f:json.dump(ledgers,f)
save('validation.json',dict(passed=True,controls=len(controls),prior_features=len(oldf),prior_changes=len(parity),source_revisions=len(revisions),validated_scenario_rows=validation))
