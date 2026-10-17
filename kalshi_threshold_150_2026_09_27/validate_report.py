import json, math
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
p=Path(__file__).resolve().parent
raw=json.loads((p/'data/recorded.json').read_text()); results=json.loads((p/'results.json').read_text())
lines=['# Cushion 1.5 replay', '', 'Coverage: '+datetime.fromtimestamp(raw['first_event'],ZoneInfo('America/New_York')).isoformat()+' through '+datetime.fromtimestamp(raw['export_ts'],ZoneInfo('America/New_York')).isoformat(), '', 'Two contracts throughout; same price, spread, timing and risk gates. P&L includes modeled taker fees. This is a counterfactual recorded-signal replay, not actual realized account P&L.', '', '| Period | Fill model | Threshold | Trades | Wins | Losses | Net P&L |', '|---|---|---:|---:|---:|---:|---:|']
for period, modes in results.items():
 for mode, scenarios in modes.items():
  for r in scenarios:
   rows=r['rows']; assert len({x['ticker'] for x in rows})==len(rows)
   assert r['wins']+r['losses']==r['trades']==len(rows)
   assert math.isclose(sum(x['pnl'] for x in rows),r['pnl'],abs_tol=1e-9)
   for x in rows:
    assert x['z']>=r['threshold'] and x['settled_ts']>=x['entry_ts']
    assert math.isclose(x['pnl'],2*(int(x['win'])-x['price'])-x['fee'],abs_tol=1e-9)
   lines.append(f"| {period} | {mode} | {r['threshold']} | {r['trades']} | {r['wins']} | {r['losses']} | ${r['pnl']:+.4f} |")
lines+=['', 'Cap-cost assumes newly eligible signals fill at the tick-valid allowed limit; their historical depth is unavailable. Next-minute uses the worse of signal ask and next-minute closing quote and rejects entries outside the allowed cap. These are two scenarios, not confidence bounds.', '', 'Recorded signals may omit alternative paths after an actual market attempt. Actual historical sizing varied from one to two contracts; these figures normalize all trades to two. Historical connectivity and cash constraints cannot be fully reconstructed. Live configuration remains unchanged.', '', 'Validation passed: unique market trades, threshold compliance, settlement chronology, and trade/aggregate P&L accounting.']
(p/'REPORT.md').write_text('\n'.join(lines)+'\n')
print('\n'.join(lines))
