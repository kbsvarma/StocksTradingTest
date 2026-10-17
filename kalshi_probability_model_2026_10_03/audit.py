"""Independent ledger cash arithmetic, frozen training parity and forecast uncertainty."""
from pathlib import Path
from decimal import Decimal,ROUND_CEILING
import collections,datetime as dt,hashlib,json
import numpy as np
import study as s
P=s.P
def main():
    z,mm=s.load();frozen=np.load(P/'inputs/fit_rows.npy');spec=json.loads((P/'models.json').read_text())
    assert np.array_equal(frozen,z[z[:,14]<2],equal_nan=True)
    assert hashlib.sha256((P/'inputs/fit_rows.npy').read_bytes()).hexdigest()==spec['fit_rows_sha256']
    metadata={m['ticker']:m for m in mm};count=0;files=0
    for path in [*P.glob('trades_*.json'),*P.glob('depth_trades_*.json')]:
        rows=json.loads(path.read_text());seen=set();files+=1
        for r in rows:
            assert r['ticker'] not in seen;seen.add(r['ticker']);m=metadata[r['ticker']]
            assert r['decision']<r['entered']<m['close']
            assert m['result']==int(m['expiration']>=m['strike'])
            won=m['result'] if r['side']==1 else 1-m['result'];assert r['won']==won
            p=Decimal(str(round(r['entry'],10)));fee=(Decimal('.07')*p*(1-p)).quantize(Decimal('.000001'),rounding=ROUND_CEILING)
            debit=(p+fee).quantize(Decimal('.0001'),rounding=ROUND_CEILING)
            assert abs(float(debit)-r['debit'])<1e-8,(path,r)
            assert abs(float(Decimal(won)-debit-Decimal('.0025'))-r['pnl'])<1e-8
            if r['model']!='market':assert r['probability']-r['debit']-.0025>=.03-1e-12
            count+=1
    preds=np.load(P/'forecasts.npz');improvement={};rng=np.random.default_rng(883)
    for ai,a in enumerate(s.ASSETS):
        msk=(z[:,15]==ai)&(z[:,14]==2);zz=z[msk];daily=collections.defaultdict(list)
        diff=(preds['market'][msk]-zz[:,13])**2-(preds['blended'][msk]-zz[:,13])**2
        for mi in np.unique(zz[:,0]):
            mask=zz[:,0]==mi;date=dt.datetime.fromtimestamp(mm[int(mi)]['close'],dt.timezone.utc).date().isoformat();daily[date].append(float(diff[mask].mean()))
        days=list(daily);sums=np.array([sum(daily[d]) for d in days]);ns=np.array([len(daily[d]) for d in days]);ix=rng.integers(0,len(days),size=(10000,len(days)));boot=sums[ix].sum(axis=1)/ns[ix].sum(axis=1)
        improvement[a]=dict(brier_improvement=float(sums.sum()/ns.sum()),day_bootstrap90=np.quantile(boot,[.05,.95]).tolist(),note='Positive is better than midpoint; clustered by date, not search-adjusted')
    s.save('audit.json',dict(status='PASS',frozen_training_rows=len(frozen),independent_ledger_rows=count,ledger_files=files,paired_prediction_improvement=improvement));print(json.dumps(dict(status='PASS',ledger_rows=count,improvement=improvement)))
if __name__=='__main__':main()
