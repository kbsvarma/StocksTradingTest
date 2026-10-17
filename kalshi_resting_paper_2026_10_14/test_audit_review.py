"""Receipt audit must distinguish rejected candidates from actual resting orders."""
import copy
import unittest
from audit_review_v2 import validate_admission_queue


class AdmissionQueueAudit(unittest.TestCase):
    def setUp(self):
        self.receipt = {'body': {'orderbook_fp': {'yes_dollars': [['0.9000','10']], 'no_dollars': [['0.0500','20']]}}}
        self.order = dict(variant='rest_1c_10s', side='yes', state='skipped', price=None,
                          filled='0', cost='0', fees='0', fill_evidence=[], queue_initial='0')

    def test_design_skip_with_admission_book_has_no_queue_price(self):
        validate_admission_queue(self.order, self.receipt)

    def test_no_price_is_not_allowed_for_an_admitted_order(self):
        for state in ('resting', 'filled', 'unfilled'):
            with self.subTest(state=state), self.assertRaises(AssertionError):
                validate_admission_queue(dict(self.order,state=state),self.receipt)

    def test_skip_cannot_hide_a_fill_or_charge(self):
        for key,value in [('filled','1'),('cost','.9'),('fees','.01'),('fill_evidence',[{'quantity':'1'}])]:
            with self.subTest(key=key), self.assertRaises(AssertionError):
                validate_admission_queue(dict(self.order,**{key:value}),self.receipt)

    def test_real_queue_is_still_checked(self):
        order=dict(self.order,state='unfilled',price='.90',queue_initial='10')
        validate_admission_queue(order,self.receipt)
        with self.assertRaises(AssertionError):
            validate_admission_queue(dict(order,queue_initial='0'),self.receipt)


if __name__ == '__main__':unittest.main()
