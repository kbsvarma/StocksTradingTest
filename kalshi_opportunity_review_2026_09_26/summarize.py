import json,collections,statistics
from pathlib import Path
P=Path(__file__).resolve().parent;s=json.loads((P/'scan_summary.json').read_text());refresh=json.loads((P/'refresh_summary.json').read_text());new={r['ticker']:r for r in refresh['results']};s['results']=[new.get(r['ticker'],r) for r in s['results']];s['refresh_observed_at']=refresh['observed_at']
for r in s['results']:
 if 'error' in r:continue
 d=json.loads((P/'data'/(r['ticker']+'.json')).read_text());events=collections.defaultdict(float)
 for m in d['settled']:events[m['event_ticker']]+=float(m.get('volume_fp') or 0)
 r['settled_events_7d']=len(events);r['traded_events_7d']=sum(v>0 for v in events.values());r['median_event_contracts']=statistics.median(events.values()) if events else 0
(P/'scan_summary.json').write_text(json.dumps(s,indent=2))
text=['# Fresh liquidity screen','',f"Observed initially: {s['observed_at']}; large-series pagination refreshed: {refresh['observed_at']}",'','98 selected series; settlement-time window September 19–25 UTC. Volume is cumulative reported contract volume for markets settling in this window, NOT dollar volume or a strict seven-day trade tape. Strike markets are grouped by event. Sports mirrored contract volumes may overlap economically. The ETH hourly scan remains capped and is explicitly a lower bound; it does not affect the non-crypto rankings. Current books are a Saturday snapshot.','', '| Series | Settled events | Events with trades | Reported contracts | Median contracts/event | Open markets | Median current spread, cents | Current markets with ≤2c spread and ≥2 size both sides |','|---|---:|---:|---:|---:|---:|---:|---:|']
for r in s['results']:
 if 'error' in r or r['contracts_7d']==0:continue
 star=' (partial)' if any(r['truncated'].values()) else ''
 text.append(f"| {r['ticker']}{star} | {r['settled_events_7d']:,} | {r['traded_events_7d']:,} | {r['contracts_7d']:,.0f} | {r['median_event_contracts']:,.0f} | {r['open']:,} | {r['median_spread_cents']} | {r['tight_2ct']} |")
text+=['','## Historical short-duration quote sample','','Eight evenly spaced traded markets per asset; only quotes 3–10 minutes before close. No depth evidence or underlying filter. Not a backtest. No spread inferred from a last trade.','', '| Series | Samples with candles / 8 | Eligible minute quotes | Median spread, cents | Markets matching price/spread/time filter | Retrieval errors |','|---|---:|---:|---:|---:|---:|']
for q in json.loads((P/'quote_sample_summary.json').read_text()):text.append(f"| {q['ticker']} | {q['candles_available']} | {q['valid_window_quotes']} | {q['median_spread_cents']} | {q['markets_with_price_spread_window_match']} | {len(q['errors'])} |")
text+=['','Missing/invalid quotes are excluded and cannot be interpreted as tradable. A sample with matching entry prices does not establish a positive return. API rate-limit failures, if any, are recorded in the quote sample summary.']
(P/'LIQUIDITY.md').write_text('\n'.join(text)+'\n')
print('Summary:',len(s['results']),'series; truncated:',[(r['ticker'],r['truncated']) for r in s['results'] if any(r.get('truncated',{}).values())])
