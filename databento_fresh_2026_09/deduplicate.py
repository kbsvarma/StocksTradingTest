from pathlib import Path
import pandas as pd,hashlib,json
P=Path(__file__).resolve().parent;f=pd.read_csv(P/'catalogue.csv');f=f[f.kind.isin(['ohlcv','quotes','statistics','definitions','raw_dbn'])&~f.path.str.startswith(('.claude/','research_snapshots/'))];rows=[]
for i,r in enumerate(f.itertuples()):
 p=P.parent/r.path;rows.append(dict(path=r.path,kind=r.kind,bytes=r.bytes,sha256=hashlib.sha256(p.read_bytes()).hexdigest()))
 if i%15000==0:print('Fingerprinted',i,flush=True)
a=pd.DataFrame(rows);a.to_csv(P/'source_fingerprints.csv',index=False);u=a.drop_duplicates('sha256');summary={'candidate_files':len(a),'exact_unique_files':len(u),'candidate_bytes':int(a.bytes.sum()),'exact_unique_bytes':int(u.bytes.sum()),'note':'Exact byte duplicates only; DBN and converted parquet or overlapping query windows are not independent coverage.'};(P/'deduplication.json').write_text(json.dumps(summary,indent=2));print(summary,flush=True)
