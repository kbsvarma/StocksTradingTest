import json,sqlite3,sys,time
from pathlib import Path
root=Path(sys.argv[1]);summary=Path(sys.argv[2]);out=Path(sys.argv[3]);out.parent.mkdir(parents=True,exist_ok=True)
results=[]
for rel in json.loads(summary.read_text())['sqlite_databases']:
 p=root/rel;item={'path':rel};t=time.time()
 try:
  with sqlite3.connect(p.as_uri()+'?mode=ro',uri=True,timeout=10) as c:
   c.execute('PRAGMA query_only=ON');rows=c.execute('PRAGMA integrity_check').fetchall();item['ok']=rows==[('ok',)];item['result']=[x[0] for x in rows[:8]]
 except Exception as e:item.update(ok=False,error=str(e))
 item['seconds']=round(time.time()-t,3);results.append(item);out.write_text(json.dumps({'checked':len(results),'ok':all(r['ok'] for r in results),'results':results},indent=2))
print(json.dumps({'checked':len(results),'failed':[r for r in results if not r['ok']]}))
