from pathlib import Path
import subprocess,json,hashlib,datetime
import databento as db
P=Path(__file__).resolve().parent
KEY=subprocess.check_output(['security','find-generic-password','-a','StocksTradingTest','-s','com.stockstradingtest.databento','-w'],text=True).strip()
client=db.Historical(KEY)
def safe_error(e):return str(e).replace(KEY,'[REDACTED]')[:1500]
def download(name,params,cap=120,ledger_name="SPEND_LEDGER.json"):
 target=P/(name+'.dbn.zst');ledger=P/ledger_name
 records=json.loads(ledger.read_text()) if ledger.exists() else []
 if any(x['name']==name for x in records):
  item=next(x for x in records if x['name']==name)
  if item['status']=='complete' and target.exists():return db.DBNStore.from_file(target)
  raise RuntimeError('Already reserved or failed; inspect before retry '+name)
 quote=float(client.metadata.get_cost(**params));reserved=quote*1.1+.01
 other=sum(x['reserved_usd'] for f in P.glob('SPEND_*.json') if f!=ledger for x in json.loads(f.read_text()))
 assert other+sum(x['reserved_usd'] for x in records)+reserved<=cap,'budget would be exceeded'
 row={'name':name,'params':params,'quote_usd':quote,'reserved_usd':reserved,'status':'reserved','at':datetime.datetime.now(datetime.timezone.utc).isoformat()};records.append(row);ledger.write_text(json.dumps(records,indent=2))
 print('REQUEST',name,'quote',quote,flush=True)
 try:
  data=client.timeseries.get_range(**params,path=target);row['status']='complete';row['bytes_on_disk']=target.stat().st_size;row['sha256']=hashlib.sha256(target.read_bytes()).hexdigest();ledger.write_text(json.dumps(records,indent=2));return data
 except Exception as e:
  row['status']='failed_reservation_retained';row['error']=safe_error(e);ledger.write_text(json.dumps(records,indent=2));raise RuntimeError(row['error']) from None
if __name__=='__main__':
 d=download('pilot_close_statistics',dict(dataset='XNAS.ITCH',symbols=['MPWR','MEDP','SMCI','VKTX'],schema='statistics',start='2018-05-01',end='2026-09-12'),cap=2)
 x=d.to_df();print(x.shape,x.columns.tolist());print(x.stat_type.value_counts().to_dict());print(x.head(3).to_string());x.to_parquet(P/'pilot_close_statistics.parquet')
