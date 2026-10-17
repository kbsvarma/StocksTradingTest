"""Evidence audit; not production unit tests. Fails on accounting/chronology errors."""
from common import *
from collections import Counter
import math
checks=Counter();errors=[]
def check(ok,label,detail=''):
 checks[label]+=1
 if not ok:errors.append({'check':label,'detail':detail})
for file in ('replay_ledgers.json.gz','weather_model_ledgers.json.gz'):
 if not (P/file).exists():continue
 for key,rows in read(P/file).items():
  ids=[r['event'] for r in rows];check(len(ids)==len(set(ids)),'unique_game_or_expiry_owner',key)
  stress=.02 if '/fees2c' in key else .01
  for r in rows:
   check(r['t']<r['entered']<r['close']<=r['settled'],'prediction_chronology',r['ticker'])
   check(0<r['entry']<=r['cap']+1e-8<1.000001,'prediction_cap',r['ticker'])
   check(abs(r['net']-(5*(int(r['won'])-r['entry'])-r['fee']-5*stress))<1e-8,'prediction_accounting',r['ticker'])
   if 'source_last' in r:
    check(r['source_last']<=r['t']-360 and r['calibration_n']>=60,'source_lag_and_calibration',r['ticker'])
   if 'score_timestamp' in r:check(r['score_timestamp']<=r['t'],'score_time',r['ticker'])
   for term in ('t','entered','close','settled','entry','cap','fee','net'):check(math.isfinite(r[term]),'finite_'+term,r['ticker'])
for file in ('combined_fx_ledgers.json.gz',):
 for key,rows in read(P/file).items():
  check(len(rows)==len({r['ticker'] for r in rows}),'fx_unique_market',key)
  stress=.02 if key.startswith('plus2c') else .01
  for r in rows:
   check(r['entered']<r['close']<=r['settled'],'fx_chronology',r['ticker'])
   check(abs(r['net']-(5*(r['won']-r['entry'])-r['fee']-5*stress))<1e-8,'fx_accounting',r['ticker'])
fees=read(P/'perps_fee_receipt.json')['response']['taker_fee_rates']
for file in (P/'perps/trades').glob('*.json'):
 rows=read(file);last_exit=0
 for r in rows:
  leg=r['leg'];n=leg['count'];ep=leg['entry'];xp=r['exit'];fee=n*(ep+xp)*fees[leg['ticker']];stress=2*max(n*.0001,n*ep*.0001);net=leg['side']*n*(xp-ep)-fee+r['funding']-stress
  check(leg['signal_ts']<r['entry_ts']<r['exit_ts'],'perp_chronology',file.name)
  check(last_exit<r['entry_ts'],'perp_no_overlap',file.name);last_exit=r['exit_ts']
  check(n>=1 and float(n).is_integer() and n*ep<=50+1e-8 and n*ep*leg['stop']<=1+1e-8,'perp_position_risk',file.name)
  check(abs(net-r['net'])<1e-8 and abs(fee-r['fees'])<1e-8,'perp_accounting',file.name)
  check(r['entry_ts']>=ts('2026-09-27T04:00:00Z'),'perp_no_warmup_trades',file.name)
# Every independently replayed family has a receipt and all fetched candle files parse.
coverage={}
for d in sorted((P/'replays').iterdir()):
 if not (d/'receipt.json').exists():continue
 q=read(d/'receipt.json');files=list(d.glob('*_candles.json.gz'));skip=list(d.glob('*_skip.json'));identities={f.name.removesuffix('_candles.json.gz') for f in files}|{f.name.removesuffix('_skip.json') for f in skip};check(len(identities)==q['events'],'event_input_coverage',d.name)
 for f in files:
  raw=read(f)
  for t,cs in raw.items():check(len(cs)==len({c['end_period_ts'] for c in cs}),'unique_candle_timestamp',t)
 coverage[d.name]={'markets':q['markets'],'events':q['events'],'candles':len(files),'skipped':len(skip)}
save(P/'validation.json',{'checks':dict(checks),'total_checks':sum(checks.values()),'errors':errors,'coverage':coverage,'limits':'Accounting and chronology checks do not prove publication latency, tradable depth, fill probability, or statistical edge.'})
print('checks',sum(checks.values()),'errors',errors[:10]);assert not errors
