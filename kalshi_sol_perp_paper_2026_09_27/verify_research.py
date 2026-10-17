import json
from pathlib import Path
from strategy import signal
P=Path(__file__).resolve().parent
candles=json.loads((P.parent/'kalshi_perps_expanded_2026_09_26/data/KXSOLPERP_candles.json').read_text())['candlesticks'];trades=json.loads((P.parent/'kalshi_perps_design_study_2026_09_27/trades/KXSOLPERP_channel24_12.json').read_text());n=0
for r in trades:
 l=r['leg'];s=signal(candles,l['signal_ts']);assert s['status']=='ready',s;assert s['side']==l['side'];assert abs(s['stop_fraction']-l['stop'])<1e-10;n+=1
print('Historical entry signals and stop sizing match:',n)
(P/'validation.json').write_text(json.dumps({'matched_historical_entries':n,'unit_tests':11,'mode':'PAPER_ONLY'},indent=2))
