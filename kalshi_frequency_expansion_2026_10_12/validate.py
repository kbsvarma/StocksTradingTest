"""Independent ledger conservation/causality audits and adverse execution fixtures."""
from core import *
import math,pickle,unittest
from sports_dynamic import exit_plan
from financial_replay import limit_price
class Fixtures(unittest.TestCase):
    def test_fee_and_complements(self):
        self.assertEqual(fee(.5),.0175);self.assertEqual(fee(.5,1,.5),.0088)
        self.assertEqual(fee(.95,5),.0167)
        self.assertEqual(quote({'yes_bid':{'close':40},'yes_ask':{'close':43}},side=-1),(.57,.6))
        self.assertAlmostEqual(limit_price(.93,1,[dict(start='0',end='1',step='.01')]),.93)
        self.assertAlmostEqual(limit_price(.93,-1,[dict(start='0',end='1',step='.01')]),.93)
    def test_no_duplicate_or_unsettled_cash(self):
        def row(i,t,end):return dict(owner=str(i),ticker=str(i),asset='fixture',side=1,t=t-3,entry_t=t,entry=.9,
                                     exit_t=end,exit_price=0,exit_kind='settlement',mult=1)
        rows=[row(i,100+i,1000) for i in range(30)]+[row(0,500,1000)]
        ledger,diag=portfolio(rows,qty=5)
        self.assertEqual(len(ledger),4) # fifth $4.58 block breaches $20 daily/open-risk budget
        self.assertEqual(len({x['owner'] for x in ledger}),4)
        self.assertGreater(diag['blocks']['daily_including_open_risk'],0)
        self.assertGreaterEqual(diag['min_cash'],2)
        self.assertLessEqual(diag['peak_reserved'],20)
    def test_stop_does_not_fill_at_stop(self):
        g=dict(rows={60:dict(close=(.50,.53),open=(.55,.58)),120:dict(close=(.20,.23),open=(.25,.28))},
               close=180,settled=200,payout=0)
        ex=exit_plan(g,3,1,.60,dict(exitkind='bracket8'),False)
        self.assertEqual(ex['exit_price'],.25);self.assertEqual(ex['exit_t'],63)
        g['rows'][120]['open']=None
        ex=exit_plan(g,3,1,.60,dict(exitkind='bracket8'),False)
        self.assertEqual(ex['exit_kind'],'settlement');self.assertEqual(ex['exit_price'],0)

def leaves(x):
    if isinstance(x,list):
        if not x or isinstance(x[0],dict) and 'entry_t' in x[0]:yield x
        else:
            for z in x:yield from leaves(z)
    elif isinstance(x,dict):
        for z in x.values():yield from leaves(z)
def audit():
    counts=C.Counter();sources=[]
    for path in sorted(P.glob('*ledgers.json.gz')):
        is_sports=path.name.startswith('sports_')
        for rows in leaves(read(path)):
            counts['portfolios']+=1;owners=set();owner_times=C.defaultdict(list);events=[]
            for i,r in enumerate(rows):
                assert r['t']<r['entry_t']<r['exit_t']
                assert 0<=r['entry']<=1 and 0<=r['exit_price']<=1
                q=D(str(r['qty']));entry=D(str(r['entry']));exit=D(str(r['exit_price']));mult=D(str(r['mult']))
                def independent_fee(px):
                    raw=D('.07')*mult*q*px*(1-px)
                    return (q*px+raw).quantize(D('.0001'),rounding=ROUND_CEILING)-q*px
                f0=independent_fee(entry);f1=D(0) if r['exit_kind']=='settlement' else independent_fee(exit)
                assert abs(float(f0+f1)-r['fees'])<1e-8
                expected=q*(exit-entry)-f0-f1-D(str(r['friction_total']))
                assert abs(float(expected)-r['net'])<1e-8
                if not is_sports:assert r['owner'] not in owners
                owners.add(r['owner']);owner_times[r['owner']].append(r)
                events.extend([(r['exit_t'],0,i),(r['entry_t'],1,i)])
                counts['ledger_rows']+=1
            for rr in owner_times.values():
                rr.sort(key=lambda r:r['entry_t'])
                if is_sports:
                    assert len(rr)<=4
                    for a,b in zip(rr,rr[1:]):assert b['entry_t']>=a['exit_t']+300
            cash=100.;active={};daily=C.defaultdict(float)
            for t,kind,i in sorted(events):
                r=rows[i];q=r['qty']
                if kind==0:
                    assert i in active;active.pop(i)
                    cash+=q*r['exit_price']-r['exit_fee']-r['exit_friction'];daily[day(t)]+=r['net']
                else:
                    cost=q*r['entry']+r['entry_fee']+r['friction_total']-r['exit_friction'];cash-=cost
                    active[i]=r;reserved=sum(a['reserve'] for a in active.values())
                    assert cash>=2-1e-7
                    assert sum(a['qty'] for a in active.values())<=25
                    assert reserved<=25+1e-7 and daily[day(t)]-reserved>=-20-1e-7
            assert not active and abs(cash-100-sum(r['net'] for r in rows))<1e-7
        sources.append(dict(path=path.name,sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
    for path in P.glob('*model_audit.json'):
        for r in read(path):
            assert r['last_settlement']<r['week'];counts['past_only_models']+=1
    save('validation.json',dict(passed=True,checks=counts,ledgers=sources,fixture_tests=3))
    print(json.dumps(dict(passed=True,checks=counts)))
if __name__=='__main__':
    suite=unittest.defaultTestLoader.loadTestsFromTestCase(Fixtures)
    if not unittest.TextTestRunner().run(suite).wasSuccessful():raise SystemExit(1)
    audit()
