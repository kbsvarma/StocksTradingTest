from common import *
import engine as e
import subprocess,hashlib,math
from collections import defaultdict,Counter
# Independent replay accounting checks across all combined portfolios.
ll=read(P/'combined_ledgers.json.gz');n=0
for sc,cases in ll.items():
 for case,rows in cases.items():
  active=[];seen=set();cash=100.;daily=defaultdict(float);stress=.02 if sc=='quote2c' else .01
  for r in sorted(rows,key=lambda r:(r['entered'],r.get('priority',0),r['asset'],r['ticker'])):
   for a in active:
    if a['settled']<=r['entered']:cash+=5*a['won'];daily[e.day(a['settled'])]+=a['net']
   active=[a for a in active if a['settled']>r['entered']]
   assert not any(a['asset']==r['asset'] for a in active)
   assert (r['asset'],r['close']) not in seen;seen.add((r['asset'],r['close']))
   risk=sum(a['reserve'] for a in active)+r['reserve'];assert risk<=25+1e-8 and len(active)<5
   assert daily[e.day(r['entered'])]-risk>=-20-1e-8
   cost=5*r['entry']+r['fee']+stress*5;assert cash>=cost-1e-8;cash-=cost
   assert r['t']<r['entered']<r['close']<=r['settled'] and r['quantity']==5
   assert r['entry']<=r['cap']+1e-8
   assert abs(r['net']-(5*r['won']-cost))<1e-8
   active.append(r);n+=1
  cash+=sum(5*r['won'] for r in active)
  assert abs(cash-100-sum(r['net'] for r in rows))<1e-7
v=read(P/'validation.json');v['combined_rows_independently_checked']=n;save(P/'validation.json',v)
# Source/arming/service preservation, no service mutation.
before=read(P/'preservation_before.json');after=dict(ts=time.time())
for k in ('files','flags'):after[k]={f:hashlib.sha256(Path(f).read_bytes()).hexdigest() for f in before[k]}
services=[line.split('=',1)[1] for line in before['services'].splitlines() if line.startswith('Id=')]
after['services']=subprocess.check_output(['systemctl','--user','show',*services,'-p','Id','-p','ActiveState','-p','MainPID','-p','NRestarts'],text=True)
after['same_files']=after['files']==before['files'];after['same_flags']=after['flags']==before['flags'];after['same_services']=after['services']==before['services']
assert after['same_files'] and after['same_flags'] and after['same_services']
save(P/'preservation_after.json',after)
# Exact cap depth from fresh snapshots. This does not apply time/z qualification.
for r in read(P/'book_summary.json'):
 if r.get('error'):continue
 original=next(x for x in read(P/'book_samples_fresh.json') if x.get('ticker')==r['ticker'])
 for b,o in zip(r['books'],original['observations']):
  if b.get('empty_side'):continue
  ask=b['ask'];side=b['side'];floor=.85 if ask<.95 else .93;cap=e.cap(ask,side,.939 if ask<.95 else .975,[dict(start='0',end='1',step='.01')]);levels=o['response']['orderbook_fp']['no_dollars' if side==1 else 'yes_dollars']
  b['exact_cap']=cap;b['depth_at_exact_cap']=sum(float(n) for p,n in levels if 1-float(p)<=cap+1e-8)
  b['price_spread_only_pass']=.85-1e-8<=ask<.98-1e-8 and ask-b['bid']<=.02000001 and cap>=ask-1e-8 and b['before_expiry']
 save(P/'books_fresh'/(r['ticker']+'_summary.json'),r)
print('VALIDATED combined',n,'rows; preserved files',len(after['files']),'flags',len(after['flags']),'services',len(services))
