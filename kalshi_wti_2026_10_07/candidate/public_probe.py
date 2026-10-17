"""Bounded public GET-only validation; no credentials or exchange client instantiated."""
import argparse
import gzip
import json
from pathlib import Path
import time
import market_data as md
from lanes import NEW_ASSETS, Z2, Z3, lane_for
from runner import MinuteQuotes

P=Path(__file__).resolve().parent.parent/'probe'

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--cycles',type=int,default=3);args=parser.parse_args()
    if not 1<=args.cycles<=10:raise ValueError('Bounded 1–10 cycles only')
    P.mkdir(exist_ok=True);base_get=md.get;counter=[0]
    def get(url):
        sent=time.time();response=base_get(url);received=time.time();counter[0]+=1
        with gzip.open(P/f'response_{time.time_ns()}.json.gz','wt') as f:
            json.dump(dict(url=url,sent_ts=sent,received_ts=received,response=response),f)
        return response
    quotes=MinuteQuotes(get);md.get=quotes
    warm=md.warm_sources();rows=[]
    print('Warmup errors',warm,flush=True)
    for cycle in range(args.cycles):
        T=(int(time.time())//60+1)*60
        # Keep this duplicate read-only scanner clear of production's +2s burst.
        time.sleep(max(0,T+12-time.time()))
        quotes.begin(T)
        for asset in NEW_ASSETS:
            # Probe complete source coverage even if signal price is gated.
            try:
                markets=md.get(md.BASE+'/markets?series_ticker=KX'+asset+'15M&status=open&limit=10')['markets']
                m=next(m for m in markets if md.timestamp(m['open_time'])<=T<md.timestamp(m['close_time']))
                sampled=md.PYTH.sample(asset,m,T,md.get,md.BASE)
                evidence=sampled[2] if sampled else None
            except Exception as e:evidence={'error':str(e)[:200]}
            for lane in (Z2,Z3):
                try:row=md.scan(asset,T,set(),lane=lane)
                except Exception as e:row=dict(asset=asset,lane=lane,decision_ts=T,status='data_error',error=str(e)[:200])
                row['probe_underlying_evidence']=evidence
                row['probe_received_ts']=time.time();rows.append(row)
        print('Cycle',cycle+1,[(r['asset'],r['lane'],r['status']) for r in rows[-2*len(NEW_ASSETS):]],flush=True)
    receipt=dict(mode='PUBLIC_GET_ONLY',cycles=args.cycles,requests=counter[0],warmup_errors=warm,
        observations=rows,complete_source_samples=sum(bool(r.get('probe_underlying_evidence',{})) and r['probe_underlying_evidence'].get('bars_count')==61 for r in rows),
        eligible_depth_observations=sum(r.get('status')=='paper_fill' for r in rows),
        errors=[r for r in rows if r.get('error') or (r.get('probe_underlying_evidence') or {}).get('error')],
        no_orders=True,finished=time.time())
    (P/'receipt.json').write_text(json.dumps(receipt,indent=2)+'\n')
    print('Complete source samples',receipt['complete_source_samples'],'/',len(rows),'eligible depth',receipt['eligible_depth_observations'],flush=True)

if __name__=='__main__':main()
