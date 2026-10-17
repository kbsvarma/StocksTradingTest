"""Bounded public-GET-only comparison; never creates an order or imports a client."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import time
from decimal import Decimal as D, ROUND_FLOOR, ROUND_CEILING

ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT/'ceiling95'))
import market_data
from execution_retry import retry_allowed
CONFIG=json.loads((ROOT/'manifest.json').read_text())['variants']


def depth_for_variant(s,variant):
    cfg=CONFIG[variant]
    if not (.85-1e-8<=s.get('signal_ask',1)<float(cfg['signal_ceiling'])-1e-8 and s.get('z',0)>=2):
        return dict(eligible=False)
    limit=min(D(str(s['signal_ask']))+D('.01'),D(cfg['execution_cap']))
    yes=limit if s['side']=='yes' else 1-limit
    cap=None
    for r in s['price_ranges']:
        lo,hi,step=map(D,(r['start'],r['end'],r['step']))
        if lo<=yes<=hi:
            px=lo+((yes-lo)/step).to_integral_value(rounding=ROUND_FLOOR if s['side']=='yes' else ROUND_CEILING)*step
            spend=px if s['side']=='yes' else 1-px
            if lo<=px<=hi and D('.85')<=spend<=limit:cap=spend;break
    if cap is None:return dict(eligible=True,error='invalid_cap')
    need=D('5');cost=D('0');low=False
    for price,qty in sorted((1-D(p),D(q)) for p,q in s.get('book_levels',[])):
        if qty<=0:continue
        if price<D('.85'):low=True
        if price>cap:break
        take=min(need,qty);need-=take;cost+=take*price
        if need==0:break
    fresh=0<=s.get('book_observed_ts',time.time())-s['decision_ts']<=35
    return dict(eligible=True,cap=str(cap),enough=need==0 and not low and fresh,
                fresh=fresh,below_floor=low,available_count=str(5-need),
                average=str(cost/(5-need)) if need<5 else None)


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--minutes',type=float,default=5)
    args=parser.parse_args()
    started=time.time();end=started+args.minutes*60
    out=ROOT/'shadow_observations.jsonl'
    last=None;done=set();cached={};checks={};rows=0
    while time.time()<end:
        now=time.time();T=int(now//60)*60
        if T!=last:
            last=T;done=set();cached={};checks={}
        if 2<=now-T<=25:
            for asset in market_data.ASSETS:
                if asset in done:continue
                try:
                    r=market_data.refresh_book(cached[asset]) if asset in cached else market_data.scan(asset,T,set())
                    checks[asset]=checks.get(asset,0)+1
                    if r['status'] in ('paper_fill','book_outside_limit','insufficient_depth'):
                        cached[asset]=dict(r)
                    r['mode']='GET_ONLY_SHADOW'
                    r['baseline_price_eligible']=r.get('signal_ask',1)<.93-1e-8
                    r['candidate_only_price']=.93-1e-8<=r.get('signal_ask',1)<.95-1e-8
                    if 'book_levels' in r:r['variant_depth']={v:depth_for_variant(r,v) for v in CONFIG}
                    # scanner book estimate is not an engine-approved order/fill.
                    r['measurement']='scanner_book_estimate_only'
                    reason='Book weakened below floor' if r.get('observed_ask',1)<.85 else ''
                    if not reason and r.get('variant_depth',{}).get('ceiling95',{}).get('enough') is False:
                        reason='Insufficient depth inside cap'
                    if not retry_allowed(r['status'],reason,checks[asset],time.time()-T,False):
                        done.add(asset)
                except Exception as e:
                    r=dict(asset=asset,decision_ts=T,mode='GET_ONLY_SHADOW',error=str(e)[:300])
                    done.add(asset)
                with out.open('a') as f:f.write(json.dumps(r)+'\n')
                rows+=1
        time.sleep(.5)
    summary=dict(started_ts=started,finished_ts=time.time(),rows=rows,mode='GET_ONLY_SHADOW',
        candidate_scanner_sha256=hashlib.sha256((ROOT/'ceiling95/market_data.py').read_bytes()).hexdigest())
    (ROOT/'shadow_summary.json').write_text(json.dumps(summary,indent=2))
    print(json.dumps(summary))


if __name__=='__main__':main()
