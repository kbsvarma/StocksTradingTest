from study import *
spec=importlib.util.spec_from_file_location('unchanged_previous_portfolio',BASE/'portfolio.py');previous=importlib.util.module_from_spec(spec);spec.loader.exec_module(previous)
C,O,F=old.build('proxy',False);S=rules(C)['LT30'];a=eng.simulate(C,O,F,S,'proxy',False,0,len(C),list(range(4)));b=previous.simulate(C,O,F,S,'proxy',False,0,len(C),list(range(4)))
assert np.allclose(a[1],b[1]) and np.allclose(a[2],b[2])
for n in [4,8]:
 C=pd.DataFrame(np.ones((3,n)));O=pd.DataFrame(np.ones((3,n*2)));F=pd.DataFrame(np.zeros((3,n)))
 r,path,pnl=eng.simulate(C,O,F,np.ones((3,n)),'proxy',False,0,3,list(range(n)))
 assert abs(r['net']+.1536)<1e-8 and abs(pnl.sum()-r['net'])<1e-8 and r['max_sampled_exposure']<.501
 z,_,_=eng.simulate(C,O,F,np.zeros((3,n)),'proxy',False,0,3,list(range(n)));assert z['net']==0 and z['orders']==0
print('PASS: previous4asset equity reproduced;4/8asset sizing, fees/funding, cash reconciliation, zero exposure')
coverage=[]
for asset in ['LTC','DOGE','ADA','BCH']:
 raw=json.load(gzip.open(P/'data'/f'{asset}.json.gz','rt'));d=pd.DataFrame(raw['rows'],columns=['t','low','high','open','close','volume'])
 assert not d.t.duplicated().any()
 assert ((d.low<=d.open)&(d.open<=d.high)&(d.low<=d.close)&(d.close<=d.high)&(d.low>0)&(d.volume>=0)).all()
 coverage.append(dict(asset=asset,rows=len(d),missing_hours=35064-len(d),source=raw['source']))
(P/'coverage.json').write_text(json.dumps(coverage,indent=2))
print('PASS: additional4assets unique timestamps and valid OHLC prices')
