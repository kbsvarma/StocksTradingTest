"""Bounded read-only post-cutover health and actual execution-path observation."""
import hashlib,json,sqlite3,subprocess,time
from pathlib import Path
from contextlib import closing
P=Path(__file__).resolve().parent;L=P.parent/'kalshi_revalidation_2026_09_12/live_v1';S=L/'state'
r=json.loads((P/'receipts/deployment_receipt.json').read_text());start=time.time();seen=[];gaps=[];good_cycles=set()
with (P/'receipts/postflight_watch.jsonl').open('w') as log:
 while time.time()-start<360:
  s=json.loads((S/'status.json').read_text());ws=s.get('market_transport',{})
  item=dict(ts=time.time(),status_age=time.time()-s['ts'],mode=s['mode'],halted=s['halted'],error=s['error'],stopped=s['stopped'],transport=ws,
   scans=[{k:x.get(k) for k in ('asset','lane','decision_ts','status','signal_qualified','book_transport','execution_book_transport','execution_status','execution_account_ms','execution_book_gap_ms')} for x in s['scans']])
  log.write(json.dumps(item)+'\n');log.flush()
  if item['status_age']>10 or item['error'] or item['stopped'] or not ws.get('connected'):gaps.append(item)
  if len(ws.get('valid_books',[]))==7:good_cycles.update(x['decision_ts'] for x in s['scans'] if x.get('decision_ts'))
  seen.append(item)
  if ws.get('stats',{}).get('book_hit',0)>0 and len(good_cycles)>=2 and len(ws.get('valid_books',[]))==7:break
  time.sleep(2)
with closing(sqlite3.connect((S/'orders.sqlite3').as_uri()+'?mode=ro',uri=True)) as db:
 rows=[json.loads(x[0]) for x in db.execute('SELECT event FROM events WHERE ts>=?',(r['verified_ts'],))]
summary=dict(start=start,end=time.time(),samples=len(seen),health_failures=len(gaps),final=seen[-1],
 valid_book_decisions=sorted(good_cycles),qualified_events=sum(bool(x.get('signal_qualified')) for x in rows),
 stream_scan_events=sum(x.get('book_transport',{}).get('source')=='websocket' for x in rows),
 stream_final_check_events=sum(x.get('execution_book_transport',{}).get('source')=='websocket' for x in rows),
 source_hashes_unchanged=all(hashlib.sha256((L/n).read_bytes()).hexdigest()==h for n,h in r['source_hashes'].items()))
(P/'receipts/postflight_watch_summary.json').write_text(json.dumps(summary,indent=2));print(json.dumps({k:v for k,v in summary.items() if k!='final'}))
