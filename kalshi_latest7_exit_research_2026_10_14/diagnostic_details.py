from pathlib import Path
import json,gzip,pickle,sys,datetime
P=Path(__file__).resolve().parent
import finalize as f
d=pickle.load(gzip.open(f.g.PREV/'inputs.pkl.gz','rb'));ss=pickle.load(gzip.open(f.g.PREV/'seconds.pkl.gz','rb'))
cc={c['id']:c for c in json.loads((P/'configs.json').read_text())}
rules={'BTC':(cc['velocity_z2_d1_l60_p0_r0-900'],.8),'WTI':(cc['absolute_1.75_g0_p60_r0-300'],.9),'NATGAS':(dict(id='NATGAS_z5_last120_limit98',family='absolute',level=5,grace=0,persist=0,lo=0,hi=120),.98),'USDCAD':(dict(id='CAD_warning_only_z075_last60',family='absolute',level=.75,grace=0,persist=0,lo=0,hi=60),0)}
out=[]
for r in d['actual']:
 if r['won'] or r['close']<f.g.SPLIT:continue
 p=f.g.prepare(r,d,ss);c,limit=rules[r['asset']];x=f.record(r,c,p,limit)
 if x['trigger'] is not None:
  j=x['trigger']-p['t'][0];x['quote_low_at_attempt']=float(p['low']['price'][j]);x['remaining']=r['close']-x['trigger'];x['reason']='modeled fill' if x['exit'] else 'quote below fixed sell limit' if x['quote_low_at_attempt']>0 else 'zero minute-low quote: fill not demonstrated'
 else:x['reason']='z/time rule did not qualify'
 out.append(x)
(P/'candidate_rejections.json').write_text(json.dumps(out,indent=2)+'\n')
for r in out:print(r['ticker'],r['reason'],r.get('remaining'),r.get('quote_low_at_attempt'),r['sell_limit'])
# USD/CAD higher warning: count all FX winners whose paths would trigger too.
xs=[]
for r in d['actual']:
 if r['asset'] not in ['EURUSD','USDCAD']:continue
 c,_=rules['USDCAD'];xs.append(f.record(r,c,f.g.prepare(r,d,ss)))
(P/'cad_fx_warning_diagnostic.json').write_text(json.dumps(dict(summary=f.summarize(xs),ledger=xs),indent=2)+'\n')
book=json.loads((P/'resting_book_coverage.json').read_text())['KXUSDCAD15M-26OCT142000-30']['books'];cad=next(r for r in out if r['asset']=='USDCAD')
coverage=dict(first_book=book[0]['received'],last_book=book[-1]['received'],warning=cad['trigger'],gap_after_last_book=cad['trigger']-book[-1]['received'],notes='Saved books end before the first z0.75 warning. Public trade prints cannot replace executable sell depth.')
(P/'cad_warning_coverage.json').write_text(json.dumps(coverage,indent=2)+'\n')
print(coverage)
