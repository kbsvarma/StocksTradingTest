"""Independent accounting/chronology and preservation checks; never writes a bot."""
from pathlib import Path
from decimal import Decimal,ROUND_CEILING
import json,gzip,hashlib,datetime as dt,subprocess
P=Path(__file__).resolve().parent
def read(p):
    if p.suffix=='.gz':
        with gzip.open(p,'rt') as f:return json.load(f)
    return json.loads(p.read_text())
def dec(x):return Decimal(str(x))
def fee(p,n,m=1):
    price=dec(p).quantize(Decimal('.0001'))
    assert abs(price-dec(p))<Decimal('1e-9')
    return (dec('.07')*dec(m)*dec(n)*price*(1-price)).quantize(Decimal('.0001'),rounding=ROUND_CEILING)
checks=0;scenarios=0;rows_checked=0;family_counts={}
for family in ['conditional','inverse','official','structural','maker','repricing']:
    led=read(P/(family+'_ledgers.json.gz'));results=read(P/(family+'_results.json'))
    if family=='structural':results=results['results']
    family_counts[family]={'scenarios':len(led),'ledger_rows':sum(len(v) for v in led.values())}
    for name,rows in led.items():
        scenarios+=1;net=Decimal(0);seen=set()
        for r in rows:
            rows_checked+=1
            assert r['event'] not in seen;seen.add(r['event'])
            assert r['t']<r['entered']<r['close']<r['settled']
            if 'calibration_end' in r:assert r['calibration_end']<=r['t'] and r['source_last']<=r['t']-600
            if family=='maker':
                n=r['size'];sides=r['filled_sides'];ff=sum((fee(r[s+'_limit'],n) for s in sides),Decimal(0))
                cost=sum((dec(n)*dec(r[s+'_limit']) for s in sides),Decimal(0))+ff+dec(n*len(sides))*dec(r['friction'])
                assert abs(dec(r['fee'])-ff)<Decimal('1e-10') and abs(cost-dec(r['cost']))<Decimal('1e-9')
                payout=n*sum(r['yes_won'] if s=='yes' else not r['yes_won'] for s in sides)
                assert payout==r['payout'];v=dec(payout)-cost
                assert r['volume']>=n*len(sides)
                for s in sides:
                    p=r[s+'_limit']
                    assert (r['low']<=p-.01+1e-9 if s=='yes' else r['high']>=1-p+.01-1e-9)
                    if 'adverse_end_quote' in name:
                        ask=r['end_quote'][1] if s=='yes' else 1-r['end_quote'][0]
                        assert ask<=p+1e-9
            elif family=='structural':
                assert r['lower_strike']<r['upper_strike'] and r['payout']>=1
                ff=fee(r['entry_yes'],5)+fee(r['entry_no'],5)
                v=5*dec(r['payout'])-5*(dec(r['entry_yes'])+dec(r['entry_no']))-ff-10*dec(r['friction'])
                assert dec(r['fee'])==ff
            else:
                ff=fee(r['entry'],5,r.get('mult',1));assert dec(r['fee'])==ff
                v=5*(dec(int(r['won']))-dec(r['entry']))-ff-5*dec(r['friction'])
                assert r['entry']<=r['cap']+1e-9 and r['entry']>=r['signal_ask']-1e-9
                if family=='official':assert r['source_last']<=r['t']-r['lag']*60 and 'Weather Company' in r['rules_primary']
            assert abs(v-dec(r['net']))<Decimal('1e-9');net+=v;checks+=1
        assert abs(net-dec(results[name]['all']['net']))<=Decimal('.00001')
        assert results[name]['all']['trades']==len(rows)
fits=read(P/'conditional_fits.json');features=read(P/'physical_features.json.gz')
labels={r['event']:r['settled'] for r in features}
for w,a in fits.items():
    assert a['training_last_settled']<int(w) and a['early_training_last_settled']<int(w)-604800
    assert all(labels[e]<int(w) for e in a['training_events'])
    assert set(a['calibration_events'])<=set(a['training_events'])
before=read(P/'preservation_before.json');live=P.parent/'kalshi_revalidation_2026_09_12/live_v1'
after={str(f.relative_to(live)):hashlib.sha256(f.read_bytes()).hexdigest() for f in live.rglob('*.py')}
assert before['source']==after
service=subprocess.check_output(['systemctl','--user','show','kalshi-crypto-z2-live.service','kalshi-tennis90-paper.service','-p','Id','-p','ActiveState','-p','MainPID','-p','NRestarts'],text=True)
assert before['service']==service
result={'checked_utc':dt.datetime.now(dt.timezone.utc).isoformat(),'scenarios':scenarios,'overlapping_ledger_rows':rows_checked,'accounting_checks':checks,
        'families':family_counts,'past_only_weekly_fits':len(fits),'live_source_files_unchanged':len(after),'service_identity_unchanged':True,
        'actual_financial_orders_submitted':0,'caveat':'Repeated scenario rows are not independent trades. Quote and maker fill assumptions are unverified.'}
(P/'verification.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2))
