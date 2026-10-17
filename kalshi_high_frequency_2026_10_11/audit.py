"""Independent per-ledger accounting/causality checks using Decimal arithmetic."""
from pathlib import Path
from decimal import Decimal,ROUND_CEILING
import json,gzip,collections
P=Path(__file__).resolve().parent
D=lambda x:Decimal(str(x))
def fee(p,m=1):return (D('.07')*D(m)*D(p)*(1-D(p))).quantize(D('.0001'),rounding=ROUND_CEILING)
def read(p):
 with gzip.open(p,'rt') as f:return json.load(f)
rows=checks=0
for mode in ['select','validate']:
 for name,rr in read(P/(mode+'_ledgers.json.gz')).items():
  config,co,scenario=name.split('|');seen=set();cost=D('.02') if scenario=='2c' else D('.01')
  for r in rr:
   assert r['close'] not in seen;seen.add(r['close']);assert r['decision']<r['entered']<r['close'];assert r['exit_ts']>r['entered']
   assert len(r['entry_prices'])==2
   assert all(e<=s+.02000001 for e,s in zip(r['entry_prices'],r['signal_prices']))
   fees=sum(fee(p) for p in r['entry_prices'])
   if r['exit_prices'] is not None:
    assert r['entered']<r['exit_decision']<r['exit_ts']<r['close']
    assert len(r['exit_prices'])==2;fees+=sum(fee(p) for p in r['exit_prices']);out=sum(map(D,r['exit_prices']));assert r['legs']==4
   else:
    assert r['exit_ts']>=r['close'];out=D(sum(x==s for x,s in zip(r['raw_settlement'],[r['direction'],-r['direction']])));assert r['legs']==2
   pnl=out-sum(map(D,r['entry_prices']))-fees-cost*r['legs']
   assert abs(float(fees)-r['fee'])<1e-8;assert abs(float(pnl)-r['pnl'])<1e-8
   rows+=1;checks+=10
 sports=read(P/('sports_'+mode+'_ledgers.json.gz'))
 for name,rr in sports.items():
  seen=set()
  for r in rr:
   assert r['owner'] not in seen;seen.add(r['owner']);assert r['t']<r['entered']<r['scheduled_start']
   assert r['entered']<r['settled'] and r['entry']<=r['cap']+1e-8
   if r['fill_type']=='unverified_maker_proxy':
    assert r['t']+120<=r['entered']<=r['t']+300
    assert r['through_price']<=r['maker_limit']-r['through_cents']/100+1e-8
   f=fee(r['entry'],r['mult']);net=D(r['payout'])-D(r['entry'])-f-D(r['friction'])
   assert abs(float(f)-r['fee'])<1e-8;assert abs(float(net)-r['net'])<1e-8
   rows+=1;checks+=7
out=dict(replayed_rows_including_repeated_scenarios=rows,assertions=checks,errors=0)
(P/'audit.json').write_text(json.dumps(out,indent=2)+'\n');print(out)
