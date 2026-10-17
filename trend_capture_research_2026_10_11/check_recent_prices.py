from portfolio import *
import requests
out=[];receipts={}
C,O,F=build('venue',False)
for asset in ASSETS:
 resp=requests.get(f'https://api.exchange.coinbase.com/products/{asset}-USD/candles',params={'granularity':86400,'start':'2026-06-01T00:00:00Z','end':'2026-10-10T00:00:00Z'},timeout=30);resp.raise_for_status();raw=resp.json();receipts[asset]=raw
 d=pd.DataFrame(raw,columns=['t','low','high','open','close','volume']);d.index=pd.to_datetime(d.t,unit='s',utc=True);d=d.sort_index();unit=float(prior.r.old.M['KX'+asset+'PERP']['contract_size']);venue=(O[asset+'_b']+O[asset+'_a'])/2/unit
 z=pd.concat([venue.rename('venue'),d.open.rename('spot')],axis=1).dropna();z=z[z.index<C.index.max()]
 for date,row in z.iterrows():out.append(dict(asset=asset,date=str(date),venue=row.venue,spot=row.spot,basis_pct=100*(row.venue/row.spot-1)))
(P/'recent_spot_receipt.json').write_text(json.dumps(receipts));pd.DataFrame(out).to_csv(P/'recent_spot_basis.csv',index=False)
df=pd.DataFrame(out);print(df.groupby('asset').basis_pct.agg(['count','min','median','max']).to_string())
