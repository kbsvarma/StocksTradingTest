import gzip,json,sys,hashlib
from pathlib import Path
from collections import defaultdict,deque,Counter
P=Path(__file__).resolve().parent;sys.path.insert(0,str(P/'candidate'))
from stream_transport import BookState
A=BookState();B=BookState();history=defaultdict(lambda:deque(maxlen=5000));pending=[];out=[];stats=Counter()
def fp(book):return hashlib.sha256(repr([(s,sorted(book[s].items())) for s in ('yes','no')]).encode()).hexdigest()
try:
 for line in gzip.open(P/'receipts/snapshot_probe_frames.jsonl.gz','rt'):
  row=json.loads(line);f=row['frame'];wall=row['received_ts'];kind=f.get('type');m=f.get('msg',{});ticker=m.get('market_ticker')
  if kind=='subscribed' and m['channel']=='orderbook_delta':A.reset();B.reset();history.clear()
  if kind not in ('orderbook_snapshot','orderbook_delta','ok'):continue
  if kind=='orderbook_snapshot' and ticker in B.books:
   temp=BookState();temp.apply(f,wall,wall);h=fp(temp.books[ticker]);bh=fp(B.books[ticker]);ah=fp(A.books[ticker]);stats['resnapshots']+=1
   stats['replace_matches']+=h==ah;stats['ignore_matches']+=h==bh
   if h!=bh or h!=ah:
    prior=[w for w,oldh in history[ticker] if oldh==h]
    entry=dict(ticker=ticker,ts=wall,seq=f['seq'],replace_matches=h==ah,ignore_matches=h==bh,prior_match_ms=1000*(wall-max(prior)) if prior else None)
    out.append(entry);pending.append((ticker,h,entry))
   B.seq[f['sid']]=f['seq']
  else:
   try:B.apply(f,wall,wall)
   except Exception as exc:
    stats['ignoring_snapshots_failed']+=1
    out.append(dict(ts=wall,error=str(exc),failing_frame=f));break
  A.apply(f,wall,wall)
  if kind=='orderbook_delta':
   h=fp(B.books[ticker]);history[ticker].append((wall,h))
   for t,ph,e in pending:
    if t==ticker and h==ph and 'future_match_ms' not in e:e['future_match_ms']=1000*(wall-e['ts'])
   pending=[x for x in pending if wall-x[2]['ts']<1 and 'future_match_ms' not in x[2]]
except EOFError:pass
report=dict(stats=dict(stats),discrepancies=out)
(P/'receipts/snapshot_audit.json').write_text(json.dumps(report,indent=2))
print(json.dumps(report,indent=2))
