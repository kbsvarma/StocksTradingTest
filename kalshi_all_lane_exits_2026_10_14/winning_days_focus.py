"""Offline 0.25-drop follow-up on October3–4 winners and matched losses.

Only the requested one rule is replayed. No API, production reads or writes.
Unknown historical books are explicitly excluded from BOTH matched results.
"""
from pathlib import Path
import gzip,json,pickle,importlib.util,hashlib,datetime,collections
from decimal import Decimal as D,ROUND_CEILING,ROUND_FLOOR
from zoneinfo import ZoneInfo
P=Path(__file__).resolve().parent;OLD=P.parent/'kalshi_fast_z_exit_2026_10_14'
spec=importlib.util.spec_from_file_location('prior_exit_replay',OLD/'replay.py')
replay=importlib.util.module_from_spec(spec);spec.loader.exec_module(replay)
ET=ZoneInfo('America/New_York');DAYS={'2026-10-03','2026-10-04'}

def aggregate(rows):
 return dict(trades=len(rows),wins=sum(r['won'] for r in rows),losses=sum(not r['won'] for r in rows),cut=sum(r['exit'] for r in rows),hold=sum(r['hold_net'] for r in rows),net=sum(r['net'] for r in rows),delta=sum(r['delta'] for r in rows),exit_fees=sum(r.get('execution',{}).get('fee',0) for r in rows),exit_friction=sum(r.get('friction',0) for r in rows))

def main():
 actual=json.loads((P/'actual_entries.json').read_text())
 allrows=[r for r in actual if datetime.datetime.fromtimestamp(r['entered'],ET).date().isoformat() in DAYS]
 raw=pickle.load(gzip.open(OLD/'inputs.pkl.gz','rb'));prepared_entries={r['ticker']:r for r in raw['cohorts']['actual']}
 prior=json.load(gzip.open(P/'depth_grid_ledgers.json.gz','rt'))['i1_drop_0.25_g0_p0'];lookup={r['ticker']:r for r in prior}
 rows=[];excluded=[]
 for entry in allrows:
  ticker=entry['ticker']
  if ticker not in prepared_entries:
   excluded.append(dict(ticker=ticker,asset=entry['asset'],won=entry['won'],hold_net=entry['hold_net'],reason='No complete matching continuous first-arrival source and sell-book history for this holding period. No hypothetical fill or unchanged P&L imputed.'))
   continue
  r=replay.prepare(prepared_entries[ticker],raw['bundles'][ticker],raw['prices'][entry['asset']])
  x=replay.simulate(r,1,.25,0,0,feed='5hz',latency=1,friction=.01)
  old=lookup[ticker];assert abs(x['net']-old['net'])<1e-10 and x['exit']==old['exit']
  assert abs(x['hold_net']-entry['hold_net'])<1e-10
  assert abs(x['net']-x['hold_net']-x['delta'])<1e-10
  if x['exit']:
   t=x['trigger'];assert t==old['trigger'];stop=r['entry_z']-.25
   assert all(r['z'][tt]['5hz']>=stop for tt in range(int(r['entered'])+1,t))
   assert r['z'][t]['5hz']<stop
   levels=x['execution']['levels'];q=sum(D(str(q)) for p,q in levels);assert q==D(str(entry['quantity']))
   gross=sum(D(str(p))*D(str(q)) for p,q in levels)
   fee=sum((D('.07')*D(str(q))*D(str(p))*(1-D(str(p)))).quantize(D('.000001'),rounding=ROUND_CEILING) for p,q in levels)
   cash=(gross-fee).quantize(D('.0001'),rounding=ROUND_FLOOR)-q*D('.01')
   basis=D(str(entry['cost']))+D(str(entry['entry_fee']))
   assert abs(float(cash-basis)-x['net'])<1e-9
   x.update(exit_price=float(gross/q),exit_after_entry=x['book_after']-entry['entered'],cash_recovered=float(cash))
  assert x['missed_source']==x['missed_book']==x['missed_depth']==0
  rows.append(x)
 wins=[r for r in rows if r['won']];losses=[r for r in rows if not r['won']];cut=[r for r in wins if r['exit']];uncut=[r for r in wins if not r['exit']]
 summary={k:aggregate(v) for k,v in [('all',rows),('all_winners',wins),('cut_winners',cut),('uncut_winners',uncut),('original_losses',losses)]}
 assert len(allrows)==44 and sum(r['won'] for r in allrows)==42
 assert len(rows)==20 and len(wins)==18 and len(losses)==2 and len(cut)==11 and len(excluded)==24
 assert abs(summary['original_losses']['delta']-7.5341)<1e-9
 assert abs(summary['all']['net']-(summary['all']['hold']+summary['original_losses']['delta']+summary['all_winners']['delta']))<1e-9
 coverage=dict(all_days_trades=len(allrows),all_days_winners=sum(r['won'] for r in allrows),all_days_losses=sum(not r['won'] for r in allrows),actual_all_days_hold=sum(r['hold_net'] for r in allrows),covered_trades=len(rows),covered_winners=len(wins),covered_losses=len(losses),excluded_winners=len(excluded),excluded_by_asset=dict(collections.Counter(r['asset'] for r in excluded)),full_days_exit_pnl=None)
 result=dict(rule=dict(drop=.25,poll_seconds=1,min_hold=0,confirmation=0,execution_delay_seconds=1,exit_friction_per_contract=.01),method='Same recorded first-arrival CF5Hz anchored to entryCoinbase, frozen entry volatility, worse before/after held-side depth, actual filled quantities, taker fees. Hypothetical execution from archived depth, not an actual sale.',coverage=coverage,summary=summary,by_asset={a:aggregate([r for r in rows if r['asset']==a]) for a in ['BTC','ETH']},by_day={d:aggregate([r for r in rows if datetime.datetime.fromtimestamp(r['entered'],ET).date().isoformat()==d]) for d in sorted(DAYS)},ledger=rows,excluded=excluded,audit=dict(prior_grid_matches=len(rows),independent_decimal_exits=sum(r['exit'] for r in rows),first_crossing_checks=sum(r['exit'] for r in rows),missing_source=0,missing_books=0,missing_depth=0,source_sha256=hashlib.sha256((OLD/'inputs.pkl.gz').read_bytes()).hexdigest()))
 (P/'winning_days_focus.json').write_text(json.dumps(result,indent=2)+'\n')
 lines=['WINNING-TRADE FOLLOW-UP: October 3–4, 2026 ET','',result['method'],'','Rule: entry z minus0.25; check every1second; no minimum holding or confirmation delay;1second execution latency. No other rule tested.','',f"Coverage:42 actual winners+2losses across the full two dates.18winners+2losses have matching archived source/book coverage.24winners are not executable-replayable (12XRP,9SOL,1ETH,2BTC). Do not label the20-trade result as all44trades.",'','Covered winner results:',json.dumps(summary,indent=2),'','Every cut winner:','Ticker | Lane | Quantity | Hold profit | Modeled exit net | Profit difference | Exit cents | Exit after entry']
 for r in cut:lines.append(f"{r['ticker']} | {r['lane']} | {r['quantity']} | {r['hold_net']:+.4f} | {r['net']:+.4f} | {r['delta']:+.4f} | {100*r['exit_price']:.2f} | {r['exit_after_entry']:.3f}s")
 lines+=['','Combined arithmetic for matched20trades:',f"Holding: +6.0482 from18winners −8.9459 from2losses = −2.8977.",f"Exit rule: +2.2375 from7uncutwinners −4.7452 from11cutwinners −1.4118 remaining on2losses = −3.9195.",f"Change: +7.5341 saved onlosers −8.5559 damage onwinners = −1.0218.",f"Winner damage:3.8107 missedprofit +4.7452 newlyrealizedlosses =8.5559.",'Entry and exit fees plus modeled exit friction already included. Actual filled quantities preserved; one historical BTC position filled3.57contracts, the rest5.','Two BTC losses failed today\'s original BTCz3entrygate; no claim that this is a gain available to currentliveentries.','This rule performs worse in the matched sample. The remaining24winner hypothetical exit P&L is unknown, not assumed zero or unchanged.','No live/paper bot file, service, order or configuration was accessed or changed by this offline replay.','',json.dumps(result['audit'],indent=2)]
 (P/'WINNING_DAYS_FOCUS.txt').write_text('\n'.join(lines)+'\n')
 print(json.dumps(dict(coverage=coverage,summary=summary,audit=result['audit']),indent=2))
if __name__=='__main__':main()
