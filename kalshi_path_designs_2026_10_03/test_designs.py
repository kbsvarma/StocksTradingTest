import copy,unittest
from designs import *
from impulse import impulse_features,eligible
import staged

def feature(t,z=1.5,spot=101.,side=1,ask=.9,rem=6):
    return dict(t=t,z=z,spot=spot,side=side,ask=ask,bid=ask-.01,rem=rem,ref=100.,sigma=.003)
def market(fs,asset='BTC',close=2000):return dict(asset=asset,close=close,features=fs)
def event(t=100,market='KXBTC15M-X',lane=2,asset='BTC',side=1,won=1):
    return dict(ticker=market,asset=asset,lane=lane,side=side,won=won,entered=t,t=t,settled=300,close=280,day='1970-01-01',cap=.939,fills={n:(.90,s.fee(.90,n)) for n in [1,2,3,4,5]})

class SignalTests(unittest.TestCase):
    def test_contiguous_persistence(self):
        m=augment([market([feature(60,z=1.3),feature(120,z=1.4),feature(180,z=1.5)])])[0]
        self.assertEqual(m['features'][-1]['persist2'],1.4);self.assertEqual(m['features'][-1]['persist3'],1.3)
    def test_gap_resets(self):
        m=augment([market([feature(60),feature(180)])])[0]
        self.assertEqual(m['features'][-1]['persist2'],-99)
    def test_direction_flip_resets(self):
        m=augment([market([feature(60,side=-1),feature(120)])])[0]
        self.assertEqual(m['features'][-1]['persist2'],-99)
    def test_arm_must_precede_pullback(self):
        m=augment([market([feature(60,z=1.5,ask=.85),feature(120,z=3,ask=.95)])])[0]
        self.assertEqual(m['features'][0]['discount'],-99)
    def test_pullback_uses_observed_prior_strong_signal(self):
        m=augment([market([feature(60,z=3,ask=.94),feature(120,z=1.5,ask=.88)])])[0]
        self.assertAlmostEqual(m['features'][1]['discount'],.06)
    def test_other_asset_requires_same_window_and_side(self):
        ms=augment([market([feature(60)]),market([feature(60,z=3,side=-1)],'ETH')]);self.assertEqual(ms[0]['features'][0]['other_z'],-99)
        ms=augment([market([feature(60)]),market([feature(60,z=3)],'ETH',close=3000)]);self.assertEqual(ms[0]['features'][0]['other_z'],-99)
    def test_future_poison_does_not_change_earlier_signals(self):
        old=[market([feature(60),feature(120)])];new=copy.deepcopy(old);new[0]['features'].append(feature(180,z=99,spot=999))
        a=impulse_features(augment(old));b=impulse_features(augment(new));self.assertEqual(a[0]['features'],b[0]['features'][:2])
    def test_sigma_reference_and_clock_adjustment(self):
        prev=feature(60,spot=100.5,rem=7);f=feature(120,spot=100.6,rem=6)
        ms=impulse_features([market([prev,f])]);g=ms[0]['features'][1]
        self.assertAlmostEqual(g['impulse1'],math.log(100.6/100.5)/.003)
        self.assertAlmostEqual(g['previous_clock_adjusted_z'],(.005)/(.003*math.sqrt(6)))
    def test_selected_signal_boundaries(self):
        c=staged.C;f=feature(60,z=1.25,ask=.85,rem=5);f['impulse1']=.25
        self.assertTrue(eligible(c,f,'BTC'));f['impulse1']=.249;self.assertFalse(eligible(c,f,'BTC'))
        f['impulse1']=.25;f['rem']=4;self.assertFalse(eligible(c,f,'BTC'))
    def test_multi_level_exact_fee_used(self):
        # Candidate size2 carries an exact multi-level fee; risk model must use it.
        from path_risk import simulate_exact
        a=np.array([[0,0,0,2,100,300,280,1,1,6,2,.90,.939,1,.01,0]],float)
        self.assertEqual(list(simulate_exact(a,0,.75,1,14,15,0,third_n=2)),[0])

class StagedTests(unittest.TestCase):
    def test_one_then_four(self):
        rr=staged.simulate([event(),event(120,lane=0)],1)
        self.assertEqual([r['quantity'] for r in rr],[1,4])
    def test_two_then_three(self):
        rr=staged.simulate([event(),event(120,lane=1)],2)
        self.assertEqual([r['quantity'] for r in rr],[2,3])
    def test_max_one_upgrade(self):
        rr=staged.simulate([event(),event(120,lane=0),event(140,lane=1)],1)
        self.assertEqual([r['quantity'] for r in rr],[1,4])
    def test_opposite_side_rejected(self):
        rr=staged.simulate([event(),event(120,lane=0,side=-1)],1);self.assertEqual(len(rr),1)
    def test_no_starter_after_existing_lane(self):
        rr=staged.simulate([event(lane=0),event(120)],1);self.assertEqual([r['quantity'] for r in rr],[5])
    def test_shared_risk_blocks_upgrade(self):
        ee=[event(),event(101,'KXETH15M-X',lane=1,asset='ETH'),event(102,'KXSOL15M-X',lane=2,asset='SOL'),event(120,lane=1)]
        rr=staged.simulate(ee,1);self.assertFalse(any(r['upgrade'] for r in rr))
    def test_no_upgrade_after_settlement(self):
        rr=staged.simulate([event(),event(301,lane=0)],1);self.assertEqual(len(rr),1)
    def test_one_small_loss_not_five(self):
        rr=staged.simulate([event(won=0)],1);self.assertEqual(rr[0]['quantity'],1);self.assertLess(abs(rr[0]['net']),1)

if __name__=='__main__':unittest.main()
