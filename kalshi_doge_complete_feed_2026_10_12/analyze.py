"""Full-input matched DOGE replay. Offline; no live modules or order clients."""
import collections as C
import copy
import datetime as dt
from decimal import Decimal as D, ROUND_CEILING, ROUND_HALF_UP
import gzip
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import statistics
import sys
import pull as p
sys.dont_write_bytecode=True
P=p.P
spec=importlib.util.spec_from_file_location('frozen_replay',P/'model/replay.py')
r=importlib.util.module_from_spec(spec);spec.loader.exec_module(r)
PRIOR_END=p.ts('2026-10-09T15:00:00Z')

def save(n,v):
    (P/n).write_text(json.dumps(v,indent=2,allow_nan=False)+'\n')

def source():
    seconds={}; counts=C.Counter(); conflicts=[]; receipts=[]
    for path in sorted((P/'data/index_hours').glob('*.json.gz')):
        receipt=json.load(gzip.open(path,'rt')); hour=receipt['hour']
        body=receipt['response']
        assert hashlib.sha256(json.dumps(body,sort_keys=True).encode()).hexdigest()==receipt['response_sha256']
        receipts.append(hour)
        for point in body['data']['payload']:
            t=int(point['time']); value=D(point['value'])
            assert value.is_finite() and value>0
            counts['raw_points']+=1
            if t%1000:continue
            t//=1000
            assert hour<=t<hour+3600
            if t in seconds:
                counts['duplicate_whole_seconds']+=1
                if seconds[t]!=value:conflicts.append(t)
            seconds[t]=value
    expected=list(range(p.ts('2026-09-30T22:00:00Z'),p.ts('2026-10-12T05:00:00Z'),3600))
    assert sorted(receipts)==expected,('incomplete hour downloads',len(receipts),len(expected))
    assert not conflicts,conflicts
    close={}; mean={}; gaps=[]
    for t in range(expected[0],expected[-1]+3600,60):
        values=[seconds[s] for s in range(t,t+60) if s in seconds]
        if len(values)==60:
            close[t]=float(values[-1]);mean[t]=sum(values)/60
        else:gaps.append(dict(t=t,seconds=len(values)))
    counts.update(hours=len(receipts),whole_seconds=len(seconds),complete_minutes=len(close),incomplete_minutes=len(gaps))
    save('index_coverage.json',dict(counts=counts,incomplete=gaps,conflicts=conflicts,
        expected_primary_minutes=(p.END-p.START)//60,missing_primary_minutes=sum(t not in close for t in range(p.START,p.END,60))))
    save('index_minutes.json',[dict(t=t,close=close[t],mean=str(mean[t])) for t in sorted(close)])
    cb={}; revisions=[]
    for batch in map(json.loads,(p.DATA/'spot_DOGE.jsonl').read_text().splitlines()):
        for row in batch['rows']:
            t,v=int(row[0]),float(row[4]); assert t%60==0 and math.isfinite(v) and v>0
            if t in cb and cb[t]!=v:revisions.append(t)
            cb[t]=v
    assert not revisions,revisions
    return cb,close,mean

def build(bars,reference_means=None):
    quotes={b['ticker']:{int(x['t']):x for x in b['candles']}
        for b in map(json.loads,(p.DATA/'candles_DOGE.jsonl').read_text().splitlines())}
    series=json.loads((p.DATA/'series_DOGE.json').read_text())['response']['series']
    assert series['fee_type']=='quadratic'
    q=C.Counter();ms=[]
    for raw in json.loads((p.DATA/'markets_DOGE.json').read_text()):
        start,close=p.ts(raw['open_time']),p.ts(raw['close_time'])
        if not p.START<=close<p.END:continue
        assert close-start==900 and raw['result'] in ('yes','no') and float(raw['notional_value_dollars'])==1
        m=dict(ticker=raw['ticker'],asset='DOGE',start=start,close=close,settled=p.ts(raw['settlement_ts']),
            result=1 if raw['result']=='yes' else -1,ranges=raw['price_ranges'],candles=quotes.get(raw['ticker'],{}),
            multiplier=float(series['fee_multiplier']),features=[],quote_features=[],volume=float(raw['volume_fp']))
        q['markets']+=1
        for rem in range(10,2,-1):
            T=close-rem*60;pair=r.quote(m['candles'].get(T));q['decision_minutes']+=1
            if pair is None:q['missing_quote']+=1;continue
            side=1 if sum(pair)>1 else -1;bid,ask=pair if side==1 else (1-pair[1],1-pair[0])
            f=dict(t=T,rem=rem,side=side,bid=bid,ask=ask);m['quote_features'].append(f.copy())
            priced=any(r.qualifies(dict(f,z=10),lane) for lane in (r.Z2,r.Z3))
            if priced:q['price_spread_time_qualified_minutes']+=1
            extra=r.feature(bars,start,T,rem,side)
            if extra is None:
                q['incomplete_or_zero_volatility']+=1
                if priced:q['price_qualified_source_gap']+=1
                continue
            if reference_means is not None:
                ref=float(reference_means[start-60]);extra['ref']=ref
                extra['z']=side*(extra['spot']/ref-1)/(extra['sigma']*math.sqrt(rem))
            f.update(extra);m['features'].append(f);q['feature_minutes']+=1
            if priced:q['price_qualified_complete_source']+=1
            if any(r.qualifies(f,lane) for lane in (r.Z2,r.Z3)):q['z_qualified_minutes']+=1
        ms.append(m)
    q['missing_spot_minutes']=sum(t not in bars for t in range(p.START,p.END,60))
    return ms,dict(q)

def verify_index(means):
    checks=[];counts=C.Counter()
    for raw in json.loads((p.DATA/'markets_DOGE.json').read_text()):
        a,b=p.ts(raw['open_time']),p.ts(raw['close_time'])
        if not p.START<=b<p.END:continue
        digits=int(raw['custom_strike']['round_digits']);quantum=D(1).scaleb(-digits)
        ref=means.get(a-60);final=means.get(b-60)
        out=dict(ticker=raw['ticker'],ref_available=ref is not None,final_available=final is not None)
        if ref is not None and final is not None:
            ref=ref.quantize(quantum,rounding=ROUND_HALF_UP);final=final.quantize(quantum,rounding=ROUND_HALF_UP)
            # Top-level floor_strike truncates to six decimals; precise strike is in custom_strike.
            official_ref=D(raw['custom_strike']['floor_strike']);official_final=D(raw['expiration_value'])
            out.update(reference_matches=ref==official_ref,final_matches=final==official_final,
                outcome_matches=('yes' if final>=ref else 'no')==raw['result'],
                reference_error=str(ref-official_ref),final_error=str(final-official_final))
        checks.append(out)
        for k,v in out.items():
            if v is True:counts[k]+=1
    save('settlement_integrity.json',dict(total=len(checks),counts=counts,
        mismatches=[x for x in checks if any(v is False for v in x.values())]))

def allcap(ms):
    rows=[]
    for m in ms:
        for f in m['features']:
            for lane in (r.Z2,r.Z3):
                if not r.qualifies(f,lane):continue
                cap=float(r.limit_price(dict(lane=lane,signal_ask=f['ask'],side='yes' if f['side']==1 else 'no',price_ranges=m['ranges']))[1])
                fee=r.fees(cap,5,multiplier=m['multiplier']);won=int(f['side']==m['result'])
                rows.append(dict(ticker=m['ticker'],asset='DOGE',lane=lane,t=f['t'],entered=f['t']+3,
                    close=m['close'],settled=m['settled'],side=f['side'],entry=cap,cap=cap,quantity=5,
                    fee=fee,won=won,pnl=5*(won-cap)-fee,ask=f['ask'],bid=f['bid'],rem=f['rem'],z=f['z'],mode='allcap'))
    return rows

def main():
    cb,cf,means=source();verify_index(means)
    control,cbcov=build(cb);treatment,cfcov=build(cf);avg,avgcov=build(cf,means)
    save('coverage.json',dict(coinbase=cbcov,cf_close=cfcov,cf_opening_average=avgcov))
    prior=json.loads((P/'control_results.json').read_text())['DOGE']
    old_features={(m['ticker'],f['t']):f for m in control for f in m['features']}
    common=copy.deepcopy(treatment)
    for m in common:m['features']=[f for f in m['features'] if (m['ticker'],f['t']) in old_features]
    datasets=dict(coinbase=control,cf_close=treatment,cf_opening_average=avg,cf_common_coverage=common)
    cases=[('fees_only','opening',0),('plus1c','opening',.01),('plus2c','opening',.02),
           ('cap_plus1c','cap_cost',.01),('delay60_plus1c','delay60',.01),('allcap_plus1c','allcap',.01),('allcap_plus2c','allcap',.02)]
    results={};ledgers={};validated=0;control_checks=[]
    for name,ms in datasets.items():
        results[name]={};ledgers[name]={};byid={m['ticker']:m for m in ms}
        for label,mode,friction in cases:
            pool,counts=(allcap(ms),{}) if mode=='allcap' else r.opportunities(ms,mode)
            out={};ledgers[name][label]={}
            for period,lo,hi in [('full',p.START,p.END),('prior',p.START,PRIOR_END),('extension',PRIOR_END,p.END)]:
                rows,blocks=r.portfolio([x for x in pool if lo<=x['close']<hi],friction,start_cash=100)
                metrics=r.metrics(rows);metrics.update(blocked=blocks,calendar_days=(hi-lo)/86400,
                    trades_per_calendar_day=len(rows)/((hi-lo)/86400),
                    lanes={lane:r.metrics([x for x in rows if x['lane']==lane]) for lane in (r.Z2,r.Z3)})
                out[period]=metrics;ledgers[name][label][period]=rows
                assert len(rows)==len({x['ticker'] for x in rows})
                for x in rows:
                    m=byid[x['ticker']]
                    assert x['t']<x['entered']<x['close']<=x['settled'] and x['quantity']==5
                    assert float(r.LANES[x['lane']].floor)-1e-8<=x['entry']<=x['cap']+1e-8
                    price=D(str(x['entry']));cost=5*price
                    fee=(cost+(D('.07')*D(str(m['multiplier']))*5*price*(1-price)).quantize(D('.000001'),rounding=ROUND_CEILING)).quantize(D('.0001'),rounding=ROUND_CEILING)-cost
                    assert abs(float(fee)-x['fee'])<1e-9
                    assert x['won']==int(x['side']==m['result'])
                    assert abs(float(5*(D(x['won'])-price)-fee-D(str(friction))*5)-x['net'])<1e-8
                    validated+=1
                if name=='coinbase' and label in prior:
                    expected=prior[label]['periods'][period]
                    for key in ('trades','wins','losses','net','settled_drawdown'):
                        assert abs(metrics[key]-expected[key])<1e-7,(label,period,key,metrics[key],expected[key])
                    control_checks.append([label,period])
            results[name][label]=dict(periods=out,counts=counts)
        print(name,{label:{k:results[name][label]['periods']['full'][k] for k in ('trades','wins','losses','net','settled_drawdown')} for label,_,_ in cases},flush=True)
    decompositions={}
    for label,_,_ in cases:
        old={x['ticker']:x for x in ledgers['coinbase'][label]['full']}
        new={x['ticker']:x for x in ledgers['cf_close'][label]['full']}
        added=[x for t,x in new.items() if t not in old];dropped=[x for t,x in old.items() if t not in new]
        overlap=[dict(ticker=t,old=old[t],new=new[t],net_difference=new[t]['net']-old[t]['net']) for t in old.keys() & new.keys()]
        recovered=[x for x in added if (x['ticker'],x['t']) not in old_features]
        changed=[x for x in added if (x['ticker'],x['t']) in old_features]
        decompositions[label]=dict(added=r.metrics(added),dropped=r.metrics(dropped),overlap=len(overlap),
            overlap_net_difference=sum(x['net_difference'] for x in overlap),
            recovered_at_missing_source_minute=r.metrics(recovered),new_at_common_source_minute=r.metrics(changed),
            extra_trades=len(new)-len(old),extra_net=sum(x['net'] for x in new.values())-sum(x['net'] for x in old.values()),
            added_rows=added,dropped_rows=dropped,overlap_rows=overlap)
    save('results.json',results);save('decomposition.json',decompositions)
    with gzip.open(P/'ledgers.json.gz','wt') as f:json.dump(ledgers,f,separators=(',',':'))
    # Independent signal arithmetic and causal-source range check for every CF feature.
    arithmetic=0
    for m in treatment:
        for f in m['features']:
            times=list(range(f['t']-3660,f['t'],60));assert max(times)+59<f['t']
            logs=[math.log(cf[b]/cf[a]) for a,b in zip(times,times[1:])]
            mu=sum(logs)/len(logs);sd=math.sqrt(sum((v-mu)**2 for v in logs)/len(logs))
            z=f['side']*(cf[f['t']-60]/cf[m['start']-60]-1)/sd/math.sqrt(f['rem'])
            assert abs(z-f['z'])<1e-10;arithmetic+=1
    save('validation.json',dict(passed=True,control_scenario_periods=len(control_checks),
        validated_ledger_rows=validated,independent_signal_checks=arithmetic,parameter_tuning=False,
        financial_orders=False,live_changes=False))

if __name__=='__main__':main()
