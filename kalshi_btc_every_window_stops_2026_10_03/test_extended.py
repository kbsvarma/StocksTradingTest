import unittest
import numpy as np
import test_engine as basic
from engine import entry
import path_model as pm,deadline_model as dm,book_replay as br,recovery as rec,recovery_classifier as rc

class ExtendedTests(unittest.TestCase):
    def test_predictive_features_ignore_later_path(self):
        x=basic.path();x[:,6:]=.2;before=pm.features(x,4,1);x[5:,:]=999
        np.testing.assert_array_equal(before,pm.features(x,4,1))
    def test_deadline_does_not_skip_negative_forecasts(self):
        x=basic.path();x[:,12]=.6;score=np.full(14,-.3)
        self.assertEqual(dm.dynamic_entry(x,score,.01,2)[0],12)
    def test_deadline_enters_earlier_opportunity(self):
        x=basic.path();x[:,12]=.6;score=np.full(14,-.3);score[3]=.02
        self.assertEqual(dm.dynamic_entry(x,score,.01,2)[0],3)
    def test_book_stop_accounts_for_latency_gap(self):
        x=basic.path();bb=np.array([[63.25,.59,.59,.59,.4,.4,.4],[70,.49,.49,.49,.50,.50,.50],[70.1,.45,.45,.45,.54,.54,.54],[70.3,.2,.2,.2,.79,.79,.79],[899,.1,.1,.1,.89,.89,.89]])
        bb=np.vstack(([59.,.59,.59,.59,.4,.4,.4],bb))
        r=br.replay(x,1,0.,900.,bb,np.array([0.,14.,0.]),basic.ex(),True,.25)
        self.assertTrue(r[5]);self.assertAlmostEqual(r[4],.2);self.assertAlmostEqual(r[8],63.25)
    def test_confirmation_uses_elapsed_time_not_message_count(self):
        x=basic.path();bb=np.array([[63.25,.59,.59,.59,.4,.4,.4],[70,.49,.49,.49,.50,.50,.50],[70.01,.48,.48,.48,.51,.51,.51],[100,.60,.60,.60,.39,.39,.39],[130,.49,.49,.49,.50,.50,.50],[190,.45,.45,.45,.54,.54,.54],[190.3,.3,.3,.3,.69,.69,.69],[899,.1,.1,.1,.89,.89,.89]])
        bb=np.vstack(([59.,.59,.59,.59,.4,.4,.4],bb))
        r=br.replay(x,1,0.,900.,bb,np.array([0.,14.,0.]),basic.ex(confirm=2),True,.25)
        self.assertTrue(r[5]);self.assertEqual(r[3],7)
    def test_bounded_recovery_at_most_two_entries(self):
        x=basic.path();x[1,:4]=.4,.41,.4,.41;x[2,:4]=.62,.63,.62,.63;x[3,:4]=.3,.31,.3,.31;x[4:,:4]=.7,.71,.7,.71
        r=rec.sim(x,1,np.array([0.,14.,0.]),basic.ex(),0.,0,1)
        self.assertEqual(r[4],2);self.assertEqual(r[5],4);self.assertAlmostEqual(r[2],r[1]+r[3])
    def test_reentry_requires_new_completed_observation(self):
        x=basic.path();x[1,:4]=.45,.46,.45,.46;x[2:,:4]=.61,.62,.61,.62
        r=rec.sim(x,1,np.array([0.,14.,0.]),basic.ex(),0.,0,0)
        self.assertGreater(r[12],r[11]);self.assertEqual(r[12],2)
    def test_conditional_features_are_causal(self):
        x=basic.path();x[:,6:]=.2;f=rc.feature(x,0,4,1,.6,.7,.4);x[5:,:]=999
        np.testing.assert_array_equal(f,rc.feature(x,0,4,1,.6,.7,.4))
    def test_conditional_stop_can_allow_recovery(self):
        x=basic.path();x[1:,:6]=[.1,.11,.1,.11,.1,.11];beta=np.zeros(12);beta[0]=10
        r=rc.sim(x,1,0,14,1,.1,0.,np.zeros(12),np.ones(12),beta)
        self.assertEqual(r[4],0);self.assertEqual(r[11],1)
if __name__=='__main__':unittest.main()
