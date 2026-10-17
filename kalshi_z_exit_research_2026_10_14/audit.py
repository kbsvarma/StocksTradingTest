"""Independent chronology/accounting audit of every frozen-grid exit."""
import gzip,json,math,hashlib
from pathlib import Path
from decimal import Decimal as D,ROUND_CEILING,ROUND_FLOOR
P=Path(__file__).resolve().parent
inputs=json.load(gzip.open(P/'paths.json.gz'));results=json.load(gzip.open(P/'all_ledgers.json.gz'));checks=exits=0
for group,configs in results.items():
 source={r['ticker']:r for r in inputs[group]}
 for name,rows in configs.items():
  assert len(rows)==len(source) and len({r['ticker'] for r in rows})==len(source)
  wait=int(name.split('_wait')[1].split('_')[0]);persist=int(name.split('_persist')[1])
  for r in rows:
   src=source[r['ticker']];q=D(str(src['quantity']));checks+=1
   assert r['hold_net']==src['hold_net'] and r['entry_fee']==src['entry_fee']
   assert abs(r['delta']-(r['net']-r['hold_net']))<1e-8
   if not r['exit']:
    assert r['net']==src['hold_net'];continue
   exits+=1;t=r['trigger_ts'];assert src['entered']<t<r['exit_ts']<src['close']
   assert t-src['entered']>=wait
   observations=[p for p in src['path'] if p['t']<=t and p['t']-src['entered']>=wait][-persist:]
   assert len(observations)==persist and all(x['z'] is not None and x['z']<r['stop_z'] for x in observations)
   assert all(b['t']-a['t']==60 for a,b in zip(observations,observations[1:]))
   x=observations[-1];assert r['quote_bid']==min(x['bid_close'],x['bid_next_open'])
   px=D(str(r['exit_price']));assert 0<px<=D(str(r['quote_bid']))-D('.01')+D('0.000000001')
   gross=q*px;raw=(D('.07')*D(str(src['multiplier']))*q*px*(1-px)).quantize(D('.000001'),rounding=ROUND_CEILING)
   proceeds=(gross-raw).quantize(D('.0001'),rounding=ROUND_FLOOR)
   expected=proceeds-D(str(src['cost']))-D(str(src['entry_fee']))-D(str(src['entry_friction']))
   assert abs(float(expected)-r['net'])<1e-8
   assert abs(float(gross-proceeds)-r['exit_fee'])<1e-8
   assert r['delta']<=float(q)+1e-8
receipt=dict(passed=True,grid_rules=112,ledger_rows=checks,exits_checked=exits,
  accounting='Independent Decimal sale cashflow with adverse bid and fee rounding',chronology='Strictly subsequent checks and exits before expiration; held-side z; no favorable extrema',
  limits='Quote proxies lack five-contract depth and exact subminute arrival proof; hypothetical exits, not exchange orders.')
(P/'audit.json').write_text(json.dumps(receipt,indent=2)+'\n');print(json.dumps(receipt))
