from pathlib import Path
import json,gzip,collections,datetime,hashlib
P=Path(__file__).resolve().parent
def read(name):return json.loads((P/name).read_text())
def row(label,v):return f'{label:64s} {v["trades"]:5d} {v["wins"]:4d}/{v["losses"]:<4d} {v["net"]:+10.4f} {v["drawdown"]:10.4f}'+(f' {v["trades_per_day"]:8.3f}' if 'trades_per_day' in v else '')
out=['WEATHER REBUILD — COMPLETE RESULT TABLES,2026-10-12','All figures USD;five contracts per trade;quote proxies,not actual executions.',
     'Each strategy is its own portfolio;never add PnLs or city-independent portfolios.',
     'Hold-to-settlement scenarios include modeled taker fees and stated per-contract friction.',
     'Short-hold scenarios pay modeled taker fees BOTH ways plus stated friction EACH LEG.','',
     'Design/scenario                                                    Trades  W/L         Net     Drawdown Trades/day']
for file,title in [('results.json','HOURLY LOGISTIC MODELS Sep12-Oct11,30calendar days;city warmup differs'),
                   ('short_results.json','FIVE-MINUTE TRADES Sep19-Oct11,23days;cash exhaustion throttles later trades'),
                   ('forecast_results.json','DAILY NBS FORECASTS Sep12-Oct9,28days'),
                   ('pending_results.json','LATE PENDING-STATION INPUT Oct3-Oct11,9days')]:
    obj=read(file);out+=['',title]
    for k,v in obj.items():out.append(row(k,v['all']))
    out+=['','City contributions to the PRIMARY shared portfolio (not independent city budgets):']
    primary={'results.json':'weather_market/lag10/base/cost1c/cent0',
             'short_results.json':'continuation/base/cost1c_each_leg',
             'forecast_results.json':'forecast_lag3h/market_weight0.5/base/cost1c',
             'pending_results.json':'margin60s/market_weight0.5/base/cost1c'}[file]
    for c,v in obj[primary]['cities'].items():out.append(row(c,v))
    out+=['Risk/skip diagnostics:',json.dumps({k:v for k,v in obj[primary].items() if k in ('risk','blocks','execution_skips','entry_skips','intents','signal_events','held_to_settlement')},indent=2)]
    if file=='pending_results.json':
        out+=['','Earlier7days vs final2days (both retrospective for this newly designed model):',row('Oct3-9',obj[primary]['first7']),row('Oct10-11',obj[primary]['last2'])]
        out+=['Primary day bootstrap95 interval: '+str(obj[primary]['all']['day_bootstrap95'])]
    if file=='results.json':out+=['Final2days were added after initial model freeze:',row('Oct10-11',obj[primary]['new_oct10_11'])]
    if file=='short_results.json':
        out+=['Fresh$100 only for independent Oct10-11 check;not a refill in historical continuous portfolio:']
        for k,v in obj.items():
            if '/base/cost1c_' in k:out.append(row(k,v['new_oct10_11_fresh_cash']))
    if file=='forecast_results.json':out+=['Removing capital controls is not a fix:',row('Unconstrained first entry per city/day',obj[primary]['unconstrained_first_per_event'])]
out+=['','LIVE PENDING FEED PROBE (index observations,not trades)',json.dumps(read('pending_feed_result.json'),indent=2),
      '', 'RESEARCH LIMITS',
      'No historical quote depth;opening-quote fills are optimistic proxies. Subsequent closes are separate delay scenarios.',
      'Retrospective model selection and short seasonal coverage prohibit treating a positive selected row as a proven edge.',
      'NBS is predictive input,not the contract settlement authority. Model run time is not publication first-arrival time.',
      'Station receipt is not public first-arrival. Pending raw reconstruction may differ after QC/source replacement.',
      'All-member station completeness and receipt-delay assumptions must be observed prospectively before any deployment.']
(P/'STATISTICS.txt').write_text('\n'.join(out)+'\n')
manifest={str(f.relative_to(P)):{'bytes':f.stat().st_size,'sha256':hashlib.sha256(f.read_bytes()).hexdigest()}
          for f in P.rglob('*') if f.is_file() and '__pycache__' not in f.parts and f.name!='server_manifest.json'}
(P/'server_manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
print('Statistics written;manifest files',len(manifest))
