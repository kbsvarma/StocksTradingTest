"""Independent Decimal cash audit and strict chronology for stored research ledgers."""
import collections,gzip,json
from decimal import Decimal,ROUND_CEILING,ROUND_FLOOR
import study as s
P=s.P
def cash(p,buy):
    p=Decimal(str(round(p,10)));fee=(Decimal('.07')*p*(1-p)).quantize(Decimal('.000001'),rounding=ROUND_CEILING)
    return float(((-p if buy else p)-fee).quantize(Decimal('.0001'),rounding=ROUND_FLOOR))
def main():
    _,mm=s.load();meta={m['ticker']:m for m in mm};count=0;recovery_count=0;files=0
    for path in sorted(P.glob('*trades*.json.gz')):
        rows=json.load(gzip.open(path,'rt'));seen=set();files+=1
        for r in rows:
            assert r['ticker'] not in seen,(path,r['ticker']);seen.add(r['ticker']);m=meta[r['ticker']]
            if path.name.startswith('recovery_trades'):
                v=r['values'];assert v[4] in (1,2);first=cash(v[9],True)+(cash(v[10],False) if v[11]<14 else v[10])-.0025*(1+int(v[11]<14));assert abs(first-v[1])<1e-8
                second=0.
                if v[4]==2:
                    assert v[12]>v[11]
                    second=cash(v[13],True)+(cash(v[14],False) if v[15]<14 else v[14])-.0025*(1+int(v[15]<14))
                assert abs(second-v[3])<1e-8 and abs(first+second-v[2])<1e-8;recovery_count+=1;continue
            assert m['start']<=r['decision']<r['entered']<m['close'],(path,r)
            assert r['exit_ts']>r['entered']
            if r['sold']:assert r['exit_ts']<m['close']
            else:assert r['exit_ts']>=m['close']
            assert r['settlement_win']==int(m['result']==r['side'])
            debit=cash(r['entry'],True);credit=cash(r['exit'],False) if r['sold'] else r['settlement_win']
            assert abs(debit-r['debit'])<1e-8 and abs(credit-r['credit'])<1e-8,(path,r)
            assert abs(debit+credit-r['pnl'])<1e-8 and abs(debit+r['settlement_win']-r['hold_pnl'])<1e-8
            assert r['legs']==1+int(r['sold']);count+=1
    for file in ('results.json','path_results.json','deadline_results.json','conditional_results.json','book_results.json','additional_book_results.json'):
        if not (P/file).exists():continue
        for result in json.loads((P/file).read_text()).values():
            for period in result['periods'].values():
                for scenario in period.values():
                    assert abs(scenario['net']-scenario['hold_same_entries']-scenario['exit_improvement'])<2e-6,(file,scenario)
                    assert abs(scenario['exit_improvement']-scenario['net_recovered_from_losers']+scenario['net_forfeited_on_winners'])<2e-6
    s.save('audit.json',dict(status='PASS',single_entry_rows=count,recovery_rows=recovery_count,ledger_files=files,checks=['Decimal purchase and sale fee/cash reconciliation','Actual settlement outcome versus side','No sale at or after expiration','No repeated entry in single-entry ledgers','Sequential recovery capped at two entries','Stop-benefit accounting identity against identical held entries']));print('AUDIT PASS',count,recovery_count,files,flush=True)
if __name__=='__main__':main()
