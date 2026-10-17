"""Public historical GETs and read-only coverage check; isolated receipts."""
from pathlib import Path
import json,time,gzip,sqlite3,requests,datetime
P=Path(__file__).resolve().parent;R=P.parent
rows=json.loads((P/'seven_diagnoses.json').read_text());(P/'public').mkdir(exist_ok=True)
s=requests.Session();summary=[]
for r in rows:
 t=r['ticker'];out=dict(ticker=t)
 for kind,path,params in [('market','/markets/'+t,{}),('trades','/markets/trades',dict(ticker=t,min_ts=int(r['entered']),max_ts=int(r['close']),limit=1000))]:
  f=P/'public'/(t+'_'+kind+'.json')
  if not f.exists():
   st=time.time()
   try:
    z=s.get('https://external-api.kalshi.com/trade-api/v2'+path,params=params,timeout=15)
    x=dict(started=st,received=time.time(),url=z.url,status=z.status_code,response=z.json())
   except Exception as e:x=dict(started=st,received=time.time(),error=str(e))
   f.write_text(json.dumps(x)+'\n')
  x=json.loads(f.read_text());out[kind+'_status']=x.get('status')
  if kind=='market' and 'market' in x.get('response',{}):
   m=x['response']['market'];out.update({k:m.get(k) for k in ['floor_strike','expiration_value','result','rules_primary','rules_secondary','open_time','close_time']});out['reference']=r['reference']
  elif kind=='trades':out.update(trades=len(x.get('response',{}).get('trades',[])),trades_truncated=bool(x.get('response',{}).get('cursor')))
  time.sleep(.1)
 summary.append(out)
db=R/'kalshi_resting_paper_2026_10_14/paper.sqlite'
c=sqlite3.connect(db.as_uri()+'?mode=ro',uri=True);c.execute('pragma query_only=on')
coverage={}
for r in rows:
 rr=c.execute('select id,started,received,path,status,record from receipts where path like ? order by received',('%'+r['ticker']+'%',)).fetchall()
 snaps=[]
 for ident,st,rec,path,status,raw in rr:
  if r['entered']<=rec<r['close'] and 'orderbook' in path:
   snaps.append(dict(id=ident,started=st,received=rec,path=path,status=status,payload=json.loads(gzip.decompress(raw))))
 coverage[r['ticker']]=dict(matching_http_receipts=len(rr),holding_book_receipts=len(snaps),books=snaps)
c.close()
(P/'public_summary.json').write_text(json.dumps(summary,indent=2)+'\n')
(P/'resting_book_coverage.json').write_text(json.dumps(coverage,indent=2)+'\n')
print(json.dumps(summary,indent=2));print('BOOKS',[(k,v['holding_book_receipts']) for k,v in coverage.items()])
