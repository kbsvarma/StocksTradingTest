"""Independent chronology, calibration, accounting and archival checks."""
from pathlib import Path
import sys,re,math,hashlib
from collections import Counter,defaultdict
from decimal import Decimal,ROUND_CEILING
P=Path(__file__).resolve().parent;OLD=P.parent/'kalshi_broad_review_2026_10_10';sys.path.insert(0,str(OLD))
from common import read,save,ts
checks=Counter();errors=[]
ms={};bars={};meta={}
for city in ['KXTEMPMIAH','KXTEMPCHIHS','KXTEMPLAXHS']:
    d=P/'data'/city;ms[city]={m['event_ticker']:m for m in read(d/'markets.json')};bars[city]={int(t):v for t,v in read(d/'bars.json.gz').items()};meta[city]=read(d/'series.json')
cal=read(P/'calibration.json');led=read(P/'ledgers.json.gz');res=read(P/'results.json')
for k,calibration in cal.items():
    city,variant,week=k.split('/');w=ts(week+'T04:00:00Z');lookback=int(variant.split('_')[0][1:])
    for h,info in calibration.items():
        for event in info['events']:
            m=ms[city][event]
            assert w-lookback*86400<=ts(m['close_time'])<w and ts(m['settlement_ts'])<w
            checks['past_only_calibration_labels']+=1
spec=read(OLD/'weather_paper_spec.json')
for city in ms:
    a=cal[city+'/w7_lag10/2026-10-03'];b=spec['calibrations'][city]
    for h in a:
        assert sorted(a[h]['events'])==sorted(b[h]['calibration_events'])
        checks['original_calibration_events_match']+=1
for scenario,rows in led.items():
    cost=Decimal(re.search(r'/cost(\d+)c/',scenario).group(1))/100
    seen=set();citylast={};total=Decimal(0)
    for r in sorted(rows,key=lambda r:r['entered']):
        city=r['asset'];lag=int(r['variant'].split('lag')[1]);data=bars[city]
        assert r['calibration_end']<=r['t']<r['entered']<r['close']<r['settled']<=r['released']
        assert r['source_last']==r['t']-lag*60
        assert all(t in data for t in range(r['source_last']-3600,r['source_last']+1,60))
        assert data[r['source_last']][1]=='normal' and data[r['source_last']][0]==r['forecast']
        assert r['entry']<=r['cap']+1e-8 and r['event'] not in seen
        if scenario.endswith('lock1'):
            assert citylast.get(city,0)<=r['entered'];citylast[city]=r['released']
        p=Decimal(str(round(r['entry'],4)));mult=Decimal(str(meta[city]['fee_multiplier']))
        f=(Decimal('.07')*mult*5*p*(1-p)).quantize(Decimal('.0001'),rounding=ROUND_CEILING)
        assert abs(float(f)-r['fee'])<1e-8
        net=5*(int(r['won'])-p)-f-5*cost;assert abs(float(net)-r['net'])<1e-8
        total+=net;seen.add(r['event']);checks['source_order_cap_ownership_accounting']+=1
    assert abs(float(total)-res[scenario]['ALL']['net'])<.00001
    assert abs(sum(m['net'] for m in res[scenario]['cities'].values())-float(total))<.00001
    checks['portfolio_totals']+=1
base=[r for r in led['w7_lag10/base/cost1c/release0/lock1'] if r['t']>=ts('2026-10-03T04:00:00Z')]
old=read(OLD/'weather_model_ledgers.json.gz')['ALL/persistence/lag10/fees1c'];old=[r for r in old if r['asset'] in ms]
comparison={'old_n':len(old),'new_n':len(base),'same_ticker_side_time':{(r['ticker'],r['t'],r['side']) for r in old}=={(r['ticker'],r['t'],r['side']) for r in base},'old_net':sum(r['net'] for r in old),'new_net':sum(r['net'] for r in base)}
out={'checks':dict(checks),'errors':errors,'selection_week_reproduction':comparison,'spec_sha256':hashlib.sha256((OLD/'weather_paper_spec.json').read_bytes()).hexdigest(),'limits':'Does not establish publication-time history, quote depth, fills, or independent prospective profitability.'}
save(P/'verification.json',out);print(out)
