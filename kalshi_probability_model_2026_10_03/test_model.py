import math,unittest
from decimal import Decimal,ROUND_CEILING
import numpy as np
import model

class ProbabilityTests(unittest.TestCase):
    def test_final_average_variance(self):
        # Independently integrate min(s,t) for a unit-width final averaging interval.
        for remaining in (1.,2.,7.,14.):
            points=np.linspace(remaining-1,remaining,2001)
            integ=np.trapezoid(np.trapezoid(np.minimum.outer(points,points),points,axis=1),points)
            self.assertAlmostEqual(float(model.horizon(1,remaining))**2,integ,places=6)
    def test_reject_partial_final_minute(self):
        with self.assertRaises(ValueError):model.horizon(.001,.9)
    def test_gaussian_direction(self):
        np.testing.assert_allclose(model.gaussian(np.array([-2,0,2])),[.977249868,.5,.022750132],atol=1e-9)
    def test_empirical_symmetry_and_monotonicity(self):
        x,c=model.empirical_fit(np.array([-1.,2.,.5]),np.array([.2,.3,.5]));t=np.linspace(-3,3,81)
        p=model.empirical_probability(t,x,c)
        self.assertTrue(np.all(np.diff(p)<=0));np.testing.assert_allclose(p+model.empirical_probability(-t,x,c),1,atol=1e-12)
    def test_fee_rounding_against_decimal(self):
        for cents in range(10,91):
            p=Decimal(cents)/100
            for n in (1,5):
                raw=(Decimal('.07')*n*p*(1-p)).quantize(Decimal('.000001'),rounding=ROUND_CEILING)
                expected=(n*p+raw).quantize(Decimal('.0001'),rounding=ROUND_CEILING)
                self.assertAlmostEqual(float(model.debit(float(p),n)),float(expected),places=10)
    def test_cost_plus_uncertainty(self):
        cash=float(model.debit(.6))+.0025
        self.assertFalse(model.qualifies(cash+.029,.6,.01));self.assertTrue(model.qualifies(cash+.03,.6,.01))
        self.assertFalse(model.qualifies(.99,.66,.01));self.assertFalse(model.qualifies(.99,.6,.03))
    def test_logistic_combination_learns_signal(self):
        rng=np.random.default_rng(918);mid=rng.uniform(.2,.8,30000);physical=rng.uniform(.2,.8,30000)
        true=model.blend(mid,physical,np.array([.7,.4]));y=(rng.random(len(mid))<true).astype(float)
        beta=model.fit_blend(mid,physical,y,np.ones(len(mid)))
        np.testing.assert_allclose(beta,[.7,.4],atol=.05)
    def test_labels_not_forecast_inputs(self):
        import study
        z,_=study.load();sample=z[::31].copy();before=study.predict(sample)
        sample[:,6]=1e9;sample[:,13]=1-sample[:,13] # future return, final result
        after=study.predict(sample)
        for k in before:np.testing.assert_array_equal(before[k],after[k])
    def test_training_markets_each_weight_one(self):
        import study
        z,_=study.load();z=z[:111];w=study.weights(z)
        for mi in np.unique(z[:,0]):self.assertAlmostEqual(w[z[:,0]==mi].sum(),1.)
    def test_entry_and_next_quote_margin(self):
        import study
        # .60 qualifies against p=.66; subsequent .64 no longer pays the margin.
        z=np.array([[0,1000,10,100,.001,0,0,.59,.60,.63,.64,.59,.60,1,2,0],
                    [0,1060,9,100,.001,0,0,.59,.60,.59,.60,.59,.60,1,2,0],
                    [0,1120,8,100,.001,0,0,.59,.60,.59,.60,.59,.60,1,2,0]],float)
        mm=[dict(ticker='TEST',asset='BTC',cohort='later',close=1600,settled=1603)]
        rows=study.trade_rows(z,mm,{'blended':np.full(3,.66)},'blended')
        self.assertEqual(len(rows),1);self.assertEqual(rows[0]['decision'],1060)
    def test_no_delay_entry_after_expiration(self):
        import study
        z=np.array([[0,1000,1,100,.001,0,0,.59,.60,.59,.60,.59,.60,1,2,0]],float)
        rows=study.trade_rows(z,[],{'blended':np.array([.8])},'blended',delay=True)
        self.assertEqual(rows,[])
if __name__=='__main__':unittest.main()
