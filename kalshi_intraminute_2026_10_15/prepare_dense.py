"""Reuse validated book reconstructions; extract actual CF arrival receipts."""
from pathlib import Path
import gzip,json,pickle,hashlib,time
P=Path(__file__).resolve().parent;R=P.parent
S=R/'kalshi_scalp_feasibility_2026_10_14';RAW=R/'kalshi_btc_eth_quant_2026_10_02/forward_state/data'
def main():
 out=P/'dense';out.mkdir(exist_ok=True)
 markets={m['ticker']:m for m in pickle.load(open(R/'kalshi_loss_review_2026_10_14/recent_features.pickle','rb'))}
 manifest=json.loads((S/'capture_manifest.json').read_text());receipts=[]
 for i,m in enumerate(x for x in manifest if x['accepted']):
  dest=out/m['target']
  if dest.exists():continue
  cf={0:[],1:[]};raw=RAW/m['file']
  assert hashlib.sha256(raw.read_bytes()).hexdigest()==m['source_sha256']
  with gzip.open(raw,'rb') as f:
   for line in f:
    if b'"type": "cfbenchmarks_value"' not in line and b'"type":"cfbenchmarks_value"' not in line:continue
    r=json.loads(line);msg=r['msg'];a=0 if msg['index_id']=='BRTI' else 1;d=json.loads(msg['data'])
    cf[a].append([r['client_received_at_ms']/1000,d['time']/1000,float(d['value'])])
  compact=pickle.load(gzip.open(S/'compact'/m['target'],'rb'));bundles=[]
  for a,meta in compact['meta'].items():
   market=markets.get(meta['ticker'])
   if market is None:raise ValueError('Missing settled metadata '+meta['ticker'])
   books=[(r[0]/1000,tuple((p/10000,q/100) for p,q in r[3]),tuple((p/10000,q/100) for p,q in r[4])) for r in compact['rows'] if r[1]==0 and r[2]==a]
   bundles.append(dict(market=market,books=books,cf=cf[a],first=m['first']/1000,last=m['last']/1000))
  with gzip.open(dest,'wb',compresslevel=1) as f:pickle.dump(bundles,f,protocol=5)
  receipts.append(dict(file=m['file'],sha256=m['source_sha256'],target=dest.name,cf_points={a:len(v) for a,v in cf.items()},first=m['first'],last=m['last']))
  if i%10==0:print('DENSE',i+1,flush=True)
 (P/'dense_manifest.json').write_text(json.dumps(dict(raw_capture_manifest_sha256=hashlib.sha256((S/'capture_manifest.json').read_bytes()).hexdigest(),files=receipts),indent=2))
 print('DENSE DONE',len(list(out.glob('*.gz'))),flush=True)
if __name__=='__main__':main()
