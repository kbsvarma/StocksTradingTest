from pathlib import Path
import json,gzip,pickle,math,statistics,hashlib,time,bisect
P=Path(__file__).resolve().parent;R=P.parent;OLD=R/'kalshi_loss_review_2026_10_14'
S=R/'kalshi_scalp_feasibility_2026_10_14';RAW=R/'kalshi_btc_eth_quant_2026_10_02/forward_state/data'
def save(n,x):(P/n).write_text(json.dumps(x,indent=2)+'\n')
def main():
 manifest=[m for m in json.loads((S/'capture_manifest.json').read_text()) if m['accepted']]
 byclose={int(m['file'].split('_')[1]):m for m in manifest}
 prices={}
 for a in ('BTC','ETH'):
  prices[a]={}
  for x in map(json.loads,(OLD/'recent_data'/f'spot_{a}.jsonl').read_text().splitlines()):
   for row in x['rows']:prices[a][int(row[0])]=float(row[4])
 cohorts={'actual':[],'current_entry_replay':[]};excluded=[]
 for x in json.load(gzip.open(OLD/'orders.json.gz')):
  if x.get('asset') not in prices or x.get('status')!='settled' or float(x.get('filled',0))<=0 or x['close_ts'] not in byclose:continue
  s=x['signal'];c=x['close_ts'];m=byclose[c]
  if s['decision_ts']*1000<m['first']:excluded.append(dict(ticker=x['ticker'],reason='decision_precedes_complete_capture',cohort='actual'));continue
  cohorts['actual'].append(dict(ticker=x['ticker'],asset=x['asset'],lane=x.get('lane',s.get('lane','cushion_z2')),t=s['decision_ts'],entered=s.get('execution_ack_ts',x['created_ts']),close=c,side=1 if x['side']=='yes' else -1,quantity=float(x['filled']),cost=float(x['cost']),entry_fee=float(x['fees']),entry_friction=0,hold_net=float(x['pnl']),won=int(x['side']==x['result']),entry_z=s['z'],entry_sigma=s['sigma'],reference=s['strike_proxy'],entry_spot=s['spot']))
 for x in json.load(gzip.open(OLD/'recent_ledgers.json.gz'))['fees1c']['all']['btc_z300']:
  if x['asset'] not in prices or x['close'] not in byclose:continue
  if x['t']*1000<byclose[x['close']]['first']:excluded.append(dict(ticker=x['ticker'],reason='decision_precedes_complete_capture',cohort='current_entry_replay'));continue
  pp=prices[x['asset']];v=[pp[t] for t in range(x['t']-3660,x['t'],60)];sig=statistics.pstdev(math.log(y/z) for z,y in zip(v,v[1:]))
  cohorts['current_entry_replay'].append(dict(ticker=x['ticker'],asset=x['asset'],lane=x['lane'],t=x['t'],entered=x['entered'],close=x['close'],side=x['side'],quantity=x['quantity'],cost=x['entry']*x['quantity'],entry_fee=x['fee'],entry_friction=x['quantity']*.01,hold_net=x['net'],won=x['won'],entry_z=x['z'],entry_sigma=sig,reference=pp[int(x['close'])-960],entry_spot=v[-1]))
 targets={r['close'] for rows in cohorts.values() for r in rows};bundles={};provenance=[]
 for i,c in enumerate(sorted(targets)):
  m=byclose[c];raw=RAW/m['file'];cf={0:{'5hz':[],'1hz':[]},1:{'5hz':[],'1hz':[]}}
  with gzip.open(raw,'rb') as f:
   for line in f:
    if b'"type": "cfbenchmarks_value' not in line and b'"type":"cfbenchmarks_value' not in line:continue
    r=json.loads(line);kind=r['type'];msg=r['msg'];a=0 if msg['index_id']=='BRTI' else 1
    if kind=='cfbenchmarks_value_5hz':src=msg['source_ts_ms'];val=float(msg['value_usd']);key='5hz'
    elif kind=='cfbenchmarks_value':d=json.loads(msg['data']);src=d['time'];val=float(d['value']);key='1hz'
    else:continue
    cf[a][key].append([r['client_received_at_ms'],src,val])
  compact=pickle.load(gzip.open(S/'compact'/m['target'],'rb'))
  for a,meta in compact['meta'].items():
   ticker=meta['ticker'];bb=[r for r in compact['rows'] if r[1]==0 and r[2]==a]
   bundles[ticker]=dict(meta=meta,books=bb,cf=cf[a])
  provenance.append(dict(file=m['file'],sha256=hashlib.sha256(raw.read_bytes()).hexdigest(),expected=m['source_sha256'],first=m['first'],last=m['last'],bad=m['bad']))
  assert provenance[-1]['sha256']==m['source_sha256']
  print('prepared',i+1,len(targets),m['file'],flush=True)
 with gzip.open(P/'inputs.pkl.gz','wb',compresslevel=1) as f:pickle.dump(dict(cohorts=cohorts,bundles=bundles,prices=prices),f)
 save('coverage.json',dict(cohorts={k:dict(n=len(v),wins=sum(r['won'] for r in v),losses=sum(not r['won'] for r in v),hold=sum(r['hold_net'] for r in v)) for k,v in cohorts.items()},excluded=excluded,provenance=provenance))
 save('entries.json',cohorts);print((P/'coverage.json').read_text()[:1800])
if __name__=='__main__':main()
