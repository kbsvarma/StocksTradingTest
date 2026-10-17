"""Replay recorded preliminary books only. This estimates request avoidance, not fills."""
import sys,json,collections
from pathlib import Path
from unittest.mock import patch
P=Path(__file__).resolve().parent;sys.path.insert(0,str(P/sys.argv[1]));import market_data
rows=json.loads((P/'evidence/before_events.json').read_text());out=[]
for r in rows:
 if r.get('execution_account_ms') is None or not r.get('book_levels') or r.get('book_observed_ts') is None:continue
 key='no_dollars' if r['side']=='yes' else 'yes_dollars'
 with patch.object(market_data.time,'time',return_value=r['book_observed_ts']),patch.object(market_data,'get',return_value=dict(orderbook_fp={key:r['book_levels']})):
  result=market_data.refresh_book(r)
 out.append(dict(ticker=r['ticker'],lane=r.get('lane'),decision_ts=r['decision_ts'],status=result['status'],actual_execution=r.get('execution_status'),original_cap=r.get('execution_cap'),new_cap=result.get('book_execution_cap')))
(P/('book_gate_'+sys.argv[1]+'.json')).write_text(json.dumps(out,indent=2));print(json.dumps(dict(version=sys.argv[1],rows=len(out),statuses=dict(collections.Counter(r['status'] for r in out)))))
