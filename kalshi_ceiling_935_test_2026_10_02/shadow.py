"""Bounded public-GET-only comparison; never creates an order or imports a client."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import time

ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT/'candidate'))
import market_data
from execution_retry import retry_allowed


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
                    r['candidate_only_price']=.93-1e-8<=r.get('signal_ask',1)<.935-1e-8
                    # scanner book estimate is not an engine-approved order/fill.
                    r['measurement']='scanner_book_estimate_only'
                    reason='Book weakened below floor' if r.get('observed_ask',1)<.85 else ''
                    if not retry_allowed(r['status'],reason,checks[asset],time.time()-T,False):
                        done.add(asset)
                except Exception as e:
                    r=dict(asset=asset,decision_ts=T,mode='GET_ONLY_SHADOW',error=str(e)[:300])
                    done.add(asset)
                with out.open('a') as f:f.write(json.dumps(r)+'\n')
                rows+=1
        time.sleep(.5)
    summary=dict(started_ts=started,finished_ts=time.time(),rows=rows,mode='GET_ONLY_SHADOW',
        candidate_scanner_sha256=hashlib.sha256((ROOT/'candidate/market_data.py').read_bytes()).hexdigest())
    (ROOT/'shadow_summary.json').write_text(json.dumps(summary,indent=2))
    print(json.dumps(summary))


if __name__=='__main__':main()
