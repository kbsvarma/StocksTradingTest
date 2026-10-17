"""Narrow offline follow-up: two recorded BTC losses, two immediate exit rules.

No live client, network calls, new entry simulation, or other-lane sweep.
"""
from pathlib import Path
import gzip,json,pickle,importlib.util,hashlib
from decimal import Decimal as D, ROUND_CEILING, ROUND_FLOOR
P=Path(__file__).resolve().parent
OLD=P.parent/'kalshi_fast_z_exit_2026_10_14'
spec=importlib.util.spec_from_file_location('prior_exit_replay',OLD/'replay.py')
replay=importlib.util.module_from_spec(spec);spec.loader.exec_module(replay)
TARGETS=['KXBTC15M-26OCT031930-30','KXBTC15M-26OCT040230-30']

def main():
 raw=pickle.load(gzip.open(OLD/'inputs.pkl.gz','rb'))
 prior=json.load(gzip.open(P/'depth_grid_ledgers.json.gz','rt'))
 rows=[]
 for ticker in TARGETS:
  entry=next(r for r in raw['cohorts']['actual'] if r['ticker']==ticker)
  assert entry['won']==0 and entry['quantity']==5
  r=replay.prepare(entry,raw['bundles'][ticker],raw['prices'][entry['asset']])
  for drop in [.25,.5]:
   result=replay.simulate(r,1,drop,0,0,feed='5hz',latency=1,friction=.01)
   old=next(x for x in prior[f'i1_drop_{drop:g}_g0_p0'] if x['ticker']==ticker)
   assert result['exit'] and result['trigger']==old['trigger']
   assert abs(result['net']-old['net'])<1e-10
   assert result['missed_source']==result['missed_book']==result['missed_depth']==0
   t=result['trigger']; stop=r['entry_z']-drop
   assert all(r['z'][tt]['5hz']>=stop for tt in range(int(r['entered'])+1,t))
   assert r['z'][t]['5hz']<stop
   # Independently audit gross, fee rounding, cash, and recovered principal.
   levels=result['execution']['levels'];q=sum(D(str(q)) for p,q in levels)
   assert q==5
   gross=sum(D(str(p))*D(str(q)) for p,q in levels)
   fee=sum((D('.07')*D(str(q))*D(str(p))*(1-D(str(p)))).quantize(D('.000001'),rounding=ROUND_CEILING) for p,q in levels)
   cash=(gross-fee).quantize(D('.0001'),rounding=ROUND_FLOOR)-D('.05')
   basis=D(str(entry['cost']))+D(str(entry['entry_fee']))
   net=cash-basis
   assert abs(float(net)-result['net'])<1e-10
   assert abs(float(cash)-result['delta'])<1e-10
   result.update(drop=drop,entry_price=entry['cost']/5,entry_outlay=float(basis),exit_price=float(gross/q),cash_recovered=float(cash),recovered_percent=float(cash/basis*100),trigger_after_entry=t-entry['entered'],exit_after_entry=result['book_after']-entry['entered'],previous_check_z=r['z'][t-1]['5hz'],checks_around_trigger=[dict(timestamp=tt,z=r['z'][tt]['5hz']) for tt in range(t-2,t+3)])
   rows.append(result)
 totals=[]
 for drop in [.25,.5]:
  xs=[x for x in rows if x['drop']==drop];cost=sum(x['entry_outlay'] for x in xs);cash=sum(x['cash_recovered'] for x in xs)
  totals.append(dict(drop=drop,positions=2,entry_outlay=cost,cash_recovered=cash,recovered_percent=100*cash/cost,remaining_net=sum(x['net'] for x in xs),exit_fees=sum(x['execution']['fee'] for x in xs),friction=sum(x['friction'] for x in xs)))
 out=dict(scope='Only two recorded BTC losses and drops0.25/0.5. Winners not tested in this narrowed follow-up.',method='One-second checks, no minimum holding or confirmation delay, recorded first-arrival CF5Hz anchored to entryCoinbase, frozenentrysigma, one-second executionlatency, worsepre/post full5contractdepth, takerexitfees+1c/contract friction. Hypothetical execution, not actual fills.',source_sha256=hashlib.sha256((OLD/'inputs.pkl.gz').read_bytes()).hexdigest(),rows=rows,totals=totals,audit='4/4 reproduce prior grid exactly; first-crossing, source/book availability,5contractdepth and independentDecimal cash/fee checks pass. No production files accessed or altered by this script.')
 (P/'two_loss_focus.json').write_text(json.dumps(out,indent=2)+'\n')
 lines=['TWO-LOSS FOCUS: small z drops, no waiting, one-second checks','',out['scope'],'',out['method'],'','Market | z drop | Entry outlay | Trigger after entry | Exit after entry | Sale cents | Cash recovered after exit fee/friction | Remaining net | Recovered %']
 for r in rows:lines.append(f"{r['ticker']} | {r['drop']} | ${r['entry_outlay']:.4f} | {r['trigger_after_entry']:.3f}s | {r['exit_after_entry']:.3f}s | {100*r['exit_price']:.1f} | ${r['cash_recovered']:.4f} | ${r['net']:.4f} | {r['recovered_percent']:.2f}%")
 lines+=['','Totals:',json.dumps(totals,indent=2),'','The first loss crosses both thresholds at the same one-second check. The second crosses0.25 about31seconds before0.5. See exact z/source/book receipts in two_loss_focus.json.','Both historical entries were below today\'s original BTCz3 gate. This isolates exit behavior on those losses; it is not a current-bot profitability claim.','Winner cuts are not measurable from a sample containing only two losers. No all-lane test or live change performed.','',out['audit']]
 (P/'TWO_LOSS_FOCUS.txt').write_text('\n'.join(lines)+'\n')
 for r in rows:print(json.dumps({k:r[k] for k in ['ticker','drop','entry_outlay','entry_z','stop_z','previous_check_z','trigger_z','trigger_after_entry','exit_after_entry','exit_price','cash_recovered','net','recovered_percent']}))
 print(json.dumps(totals))
if __name__=='__main__':main()
