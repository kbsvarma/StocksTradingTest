from common import *
from collections import Counter
D=P/'perps/data';markets=read(D/'markets.json')['markets'];rec=read(D/'receipt.json');out={};fees=read(P/'perps_fee_receipt.json')['response']
for m in markets:
 t=m['ticker'];cs=read(D/(t+'_candles.json'))['candlesticks'];fs=read(D/(t+'_funding.json'))['funding_rates'];good=[]
 for c in cs:
  try:
   b=float(c['bid']['close']);a=float(c['ask']['close'])
   if 0<b<=a<1e8:good.append(c['end_period_ts'])
  except (KeyError,TypeError,ValueError):pass
 times=[c['end_period_ts'] for c in cs];fund=[ts(f['funding_time']) for f in fs if rec['start_ts']<=ts(f['funding_time'])<=END]
 out[t]={'status':m['status'],'asset_class':m['asset_class'],'volume24_notional':m.get('volume_24h_notional_value_dollars'),'candles':len(cs),'valid_quotes':len(good),'first_quote':min(good) if good else None,'last_quote':max(good) if good else None,'funding_records_in_test':len(fund),'funding_first':min(fund) if fund else None,'funding_last':max(fund) if fund else None,'maker_rate':fees['maker_fee_rates'][t],'taker_rate':fees['taker_fee_rates'][t]}
save(P/'perps_coverage.json',out)
paper=read(P/'sol_paper_snapshot.json');hist=read(P/'perps/trades/KXSOLPERP_channel24_12.json');by={r['entry_ts']:r for r in hist};joined=[]
for r in paper['trades']:
 h=by.get(r['entry_scheduled_ts']);joined.append({'scheduled':r['entry_scheduled_ts'],'paper_entry':r['entry'],'paper_net':r['net'],'paper_exit':r['exit_ts'],'replay_entry':h['leg']['entry'] if h else None,'replay_net':h['net'] if h else None,'replay_exit':h['exit_ts'] if h else None})
save(P/'sol_forward_vs_replay.json',{'paper_net':paper['realized_pnl'],'paper_n':len(paper['trades']),'replay_net':sum(r['net'] for r in hist),'replay_n':len(hist),'same_scheduled_entry':sum(r['replay_entry'] is not None for r in joined),'rows':joined,'interpretation':'Model and forward paper are different evidence. First nine scheduled entries match, with small quote differences. Oct7 onward trade path differs. Historical replay skips entry immediately at its prior exit timestamp (busy guard); forward paper differs. No causal attribution beyond directly observed differences and no production fix attempted.'})
