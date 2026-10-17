import json,collections,math
from pathlib import Path
from replay import ts
P=Path(__file__).resolve().parent;D=P/'candidate_data';O=P/'results';coverage=[];errors=[]
for mp in sorted(D.glob('*_markets.json')):
 t=mp.name.removesuffix('_markets.json');ms=json.loads(mp.read_text());cp=D/(t+'_candles.jsonl');cs={r['ticker']:r['candles'] for r in map(json.loads,cp.read_text().splitlines())} if cp.exists() else {}
 requested=ms
 payouts=collections.Counter(m.get('settlement_value_dollars') for m in ms)
 for m in ms:
  expect=1 if m['result']=='yes' else 0
  if float(m.get('settlement_value_dollars') or 0)!=expect:errors.append({'ticker':m['ticker'],'problem':'nonbinary settlement'})
 if len({m['ticker'] for m in ms})!=len(ms):errors.append({'series':t,'problem':'duplicate market'})
 for ticker,candles in cs.items():
  if len({c['t'] for c in candles})!=len(candles):errors.append({'ticker':ticker,'problem':'duplicate candle time'})
 coverage.append({'series':t,'downloaded_markets':len(ms),'requested_candle_markets':len(requested),'returned_candle_records':len(cs),'nonempty_candle_markets':sum(bool(cs.get(m['ticker'])) for m in requested),'missing_records':sum(m['ticker'] not in cs for m in requested),'empty_candles':sum(m['ticker'] in cs and not cs[m['ticker']] for m in requested),'settlement_payouts':dict(payouts),'scope':'Full September 8-25 hourly weather; other durations per PLAN.md' if 'TEMP' in t else 'Full configured close/settlement window'})
for mode in ['open','close']:
 for r in json.loads((O/('replay_results_'+mode+'.json')).read_text()):
  trades=json.loads((O/(r['series']+'_'+r['rule']+'_'+mode+'_trades.json')).read_text())
  if len({t['event'] for t in trades})!=len(trades):errors.append({'series':r['series'],'rule':r['rule'],'problem':'duplicate risk event'})
  for t in trades:
   if not t['signal_ts']<t['entry_ts']<t['close_ts']:errors.append({'ticker':t['ticker'],'problem':'entry chronology'})
   if abs(t['net']-(2*t['won']-t['cost']))>1e-8:errors.append({'ticker':t['ticker'],'problem':'pnl identity'})
   if t['fee']<0 or not math.isfinite(t['cost']):errors.append({'ticker':t['ticker'],'problem':'fee or cost invalid'})
  portfolio=r['portfolio']
  if abs(portfolio['ending_cash']-100-portfolio['metrics'].get('stress1',0))>.00011:errors.append({'series':r['series'],'rule':r['rule'],'problem':'portfolio accounting identity'})
(O/'data_coverage.json').write_text(json.dumps(coverage,indent=2));(O/'validation.json').write_text(json.dumps({'errors':errors,'series':len(coverage),'market_rows':sum(x['downloaded_markets'] for x in coverage),'candle_records':sum(x['returned_candle_records'] for x in coverage),'missing_records':sum(x['missing_records'] for x in coverage),'empty_candle_histories':sum(x['empty_candles'] for x in coverage),'limitations':['No historical depth proof','Current fees applied retrospectively','Opening-quote proxy optimistic','Delayed close quote stress can overstate realistic latency','Final metadata may not reproduce every historical rule change','Daily weather and other one-value sources need causal forecast vintages']},indent=2));print('validation',len(errors),'errors; missing_records',sum(x['missing_records'] for x in coverage));assert not errors,errors[:5]
