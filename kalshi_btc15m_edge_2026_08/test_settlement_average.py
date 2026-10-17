import unittest

from settlement_average_study import integrated_probability


class SettlementAverageMathTests(unittest.TestCase):
    def test_certain_when_window_is_complete(self):
        self.assertEqual(integrated_probability(100.0, [101.0] * 60, 0, 1.0), 1.0)
        self.assertEqual(integrated_probability(100.0, [99.0] * 60, 0, 1.0), 0.0)

    def test_probability_is_symmetric_at_threshold(self):
        observed = [100.0] * 30
        self.assertAlmostEqual(integrated_probability(100.0, observed, 30, 2.0), 0.5)

    def test_more_favorable_observations_raise_yes_probability(self):
        low = integrated_probability(100.0, [99.0] * 30, 30, 2.0)
        high = integrated_probability(100.0, [101.0] * 30, 30, 2.0)
        self.assertLess(low, 0.5)
        self.assertGreater(high, 0.5)

    def test_window_size_is_enforced(self):
        with self.assertRaises(ValueError):
            integrated_probability(100.0, [100.0] * 29, 30, 2.0)


if __name__ == "__main__":
    unittest.main()
