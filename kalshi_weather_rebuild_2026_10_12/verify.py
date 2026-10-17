"""Independent chronology/accounting checks and live-service preservation receipt."""
from pathlib import Path
from collections import Counter
from decimal import Decimal,ROUND_CEILING
import json,gzip,time,hashlib,subprocess
P=Path(__file__).resolve().parent
def read(p):
    if p.suffix=='.gz':
        with gzip.open(p,'rt') as f:return json.load(f)
    return json.loads(p.read_text())
checks=Counter()
def modeled_fee(price,mult):
    p=Decimal(str(price));m=Decimal(str(mult))
    return float((Decimal('.07')*m*5*p*(1-p)).quantize(Decimal('.0001'),rounding=ROUND_CEILING))
for prefix in ['', 'forecast_', 'pending_']:
    lp=P/(prefix+'ledgers.json.gz');rp=P/(prefix+'results.json')
    if not lp.exists():continue
    for key,rows in read(lp).items():
        seen=set();total=Decimal(0);last_city={}
        for r in sorted(rows,key=lambda r:r['entered']):
            assert r['event'] not in seen;seen.add(r['event'])
            assert last_city.get(r['asset'],0)<=r['entered'];last_city[r['asset']]=r['released']
            assert r['calibration_end']<=r['t']<r['entered']<r['close']<r['settled']
            assert r['entry']<=r['cap']+1e-8
            if prefix=='forecast_':assert r['runtime']<=r['t']-r['lag']*3600
            elif prefix=='pending_':
                assert r['latest_station_receipt']+r['margin']<=r['t']
                assert r['config_published']<=r['t'] and r['t']-r['source_last']<=300
            else:assert r['source_last']<r['t']
            f=modeled_fee(round(r['entry'],6),r.get('fee_multiplier',r.get('mult')))
            if key.endswith('cent1'):f=float(Decimal(str(f)).quantize(Decimal('.01'),rounding=ROUND_CEILING))
            assert abs(f-r['fee'])<1e-7,(key,r['ticker'],f,r['fee'])
            n=5*(int(r['won'])-r['entry'])-f-5*r['stress']
            assert abs(n-r['net'])<1e-7
            total+=Decimal(str(r['net']));checks[prefix+'trade_checks']+=1
        assert abs(float(total)-read(rp)[key]['all']['net'])<1e-5
        checks[prefix+'portfolio_totals']+=1
if (P/'short_ledgers.json.gz').exists():
    res=read(P/'short_results.json')
    for key,rows in read(P/'short_ledgers.json.gz').items():
        seen=set();total=0
        for r in rows:
            assert r['event'] not in seen;seen.add(r['event'])
            assert r['t']<r['entered']<r['settled'] and r['entry']<=r['cap']+1e-8
            fees=r['entry_fee']+r['exit_fee'];stress=5*r['stress']*(1 if r['exit_fallback'] else 2)
            net=5*(r['exit']-r['entry'])-fees-stress
            assert abs(net-r['net'])<1e-7
            assert r['reserve']+1e-8>=5*r['entry']+r['entry_fee']+5*r['stress']
            total+=net;checks['short_accounting']+=1
        assert abs(total-res[key]['all']['net'])<1e-5;checks['short_totals']+=1
protection={}
live=P.parent/'kalshi_revalidation_2026_09_12/live_v1'
old=P.parent/'kalshi_lane_operations_2026_10_12'
for name,root in [('source_hashes.json',live),('arm_halt_hashes.json',live/'state')]:
    reference=read(old/name)
    actual={n:hashlib.sha256((root/n).read_bytes()).hexdigest() for n in reference}
    assert reference==actual;protection[name]={'unchanged':True,'files':len(reference)}
services={}
for name in ['kalshi-crypto-z2-live.service','kalshi-dashboard.service','kalshi-execution-observer.service','kalshi-tennis90-paper.service']:
    services[name]=dict(line.split('=',1) for line in subprocess.check_output(['systemctl','--user','show',name,'-p','ActiveState','-p','MainPID','-p','NRestarts'],text=True).splitlines())
status=read(live/'state/status.json')
out={'completed_ts':time.time(),'checks':dict(checks),'protected_live_files':protection,'services':services,
    'status_keys':list(status),'not_proven':['historical first arrival times','five contract book depth','real fills','out of sample durable edge']}
(P/'verification.json').write_text(json.dumps(out,indent=2)+'\n');print(json.dumps(out,indent=2))
