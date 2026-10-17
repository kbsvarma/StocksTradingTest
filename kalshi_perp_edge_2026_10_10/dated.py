"""Fixed-calendar US dated-future carry. Open MTM observations are not completed trades."""
from core import *
out=[]
for a,t in [('ET','KXETHPERP'),('BIT','KXBTCPERP')]:
 for expiry in ['26JUN26','31JUL26','28AUG26','25SEP26','30OCT26','27NOV26','24DEC26']:
  pid=f'{a}-{expiry}-CDE';p=D/'cde'/f'{pid}_meta.json'
  if not p.exists():continue
  meta=json.load(open(p));fd=meta.get('future_product_details',{});fp=D/'cde'/f'{pid}_candles.json'
  if not fd.get('contract_expiry') or not fp.exists():continue
  raw=json.load(open(fp));size=float(fd['contract_size']);qty=int(round(size/float(M[t]['contract_size'])));exp=T(fd['contract_expiry'])
  # Hourly candles report their opening timestamp. Their close is only known an hour later.
  cs={int(r['start'])+3600:float(r['close']) for r in raw['rows'] if float(r['volume'])>0 and int(r['start'])+3600<=END}
  for days in [28,56,84]:
   planned=exp-days*86400;targetend=min(exp-86400,END)
   eligible=sorted(s for s in cs if planned<=s<=planned+86400 and s<targetend and at(t,s))
   if not eligible:
    out.append({'product':pid,'entry_days':days,'status':'missing_entry_history','candles':len(cs),'errors':raw['errors']});continue
   s=eligible[0];ends=sorted(k for k in cs if s<k<=targetend and at(t,k))
   if not ends:
    out.append({'product':pid,'entry_days':days,'status':'missing_exit_history'});continue
   en=ends[-1];ep=at(t,s).ask;xp=at(t,en).bid;fc=cs[s];xc=cs[en];notional=qty*ep
   funding=-qty*sum(rate*mark for ft,(rate,mark) in F[t].items() if s<ft<=en)
   gross=qty*(xp-ep)+size*(fc-xc)
   for fee in [.15,.30,.50]:
    for slip in [2,5]:
     fees=qty*(ep+xp)*.0012+2*fee;friction=qty*(ep+xp)*.0001+size*(fc+xc)*slip/10000
     # Conservative illustration: 1x posted on EACH side, no cross-venue collateral offset.
     capital=notional+size*fc+qty*ep*.0013+fee+size*fc*slip/10000
     out.append(dict(product=pid,entry_days=days,status='closed_before_expiry' if exp-86400<=END else 'OPEN_mark_to_sample_end',entry=s,exit=en,entry_delay_hours=(s-planned)/3600,exit_quote_lag_hours=(targetend-en)/3600,kalshi_contracts=qty,cde_contracts=1,underlying_units=size,initial_notional=notional,total_capital_1x_each=capital,kalshi_entry=ep,kalshi_exit=xp,cde_entry=fc,cde_exit=xc,entry_basis_pct=(size*fc/notional-1)*100,exit_basis_pct=(size*xc/(qty*xp)-1)*100,gross=gross,funding=funding,fees=fees,friction=friction,cde_fee_side=fee,cde_slip_bp=slip,net=gross+funding-fees-friction,return_on_total_capital_pct=100*(gross+funding-fees-friction)/capital))
save('dated_results.json',out)
for r in out:
 if r.get('cde_fee_side')==.30 and r.get('cde_slip_bp')==5:print(r)
print('available scenarios',sum('net'in r for r in out),'missing entries',sum(r['status'].startswith('missing') for r in out))
