import itertools,json
import study as s
P=s.P;x,mm=s.load();d=s.features(x,mm);out={}
for drop,window in itertools.product((.01,.02,.03),((5,9),(6,8),(5,7),(7,9))):
    c=dict(asset='ETH',direction=1,filter='quote_falling',remlo=window[0],remhi=window[1],drop=drop);rr=s.rows(c,d)
    key=f'drop{drop}_time{window[0]}-{window[1]}';out[key]=dict(config=c,periods={co:dict(base=s.metrics([r for r in rr if r['cohort']==co],sum(m['asset']=='ETH' and s.PERIODS[s.period(m['close'])]==co for m in mm)),cost1c=s.metrics([r for r in rr if r['cohort']==co],sum(m['asset']=='ETH' and s.PERIODS[s.period(m['close'])]==co for m in mm),.01)) for co in s.PERIODS[:4]})
s.save('neighborhoods.json',out)
for k,r in out.items():print(k,[(co,r['periods'][co]['base']['trades'],r['periods'][co]['cost1c']['net']) for co in s.PERIODS[:4]])
