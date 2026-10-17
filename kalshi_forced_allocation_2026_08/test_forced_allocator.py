#!/usr/bin/env python3

import unittest

import numpy as np

from forced_allocator_study import (
    AssetState,
    PairState,
    candidate,
    choose_single,
    fee,
    fit_offset_model,
)


def asset(family, mid, bid, ask, result_yes):
    return AssetState(
        family=family,
        ticker=family + "-TEST",
        close_ts=1000,
        day="2026-01-01",
        result_yes=result_yes,
        rem=6,
        yes_bid=bid,
        yes_ask=ask,
        next_yes_bid=bid,
        next_yes_ask=ask,
        mid=mid,
        other_ready=True,
        mom1=0.0,
        mom3=0.0,
        spread=ask - bid,
        flips=0,
        held=5,
        path_vol=0.01,
    )


class ForcedAllocatorTests(unittest.TestCase):
    def test_residual_policy_selects_single_highest_ev_action(self):
        pair = PairState(
            1000,
            "2026-01-01",
            asset("KXBTC15M", 0.70, 0.69, 0.71, 1),
            asset("KXETH15M", 0.74, 0.73, 0.75, 1),
        )
        choice = choose_single(
            pair,
            {"KXBTC15M": 0.70, "KXETH15M": 0.82},
            "residual_model",
            0.0,
        )
        self.assertEqual(choice.family, "KXETH15M")
        self.assertEqual(choice.side, "yes")

    def test_forced_policy_still_chooses_when_all_predicted_evs_are_negative(self):
        pair = PairState(
            1000,
            "2026-01-01",
            asset("KXBTC15M", 0.51, 0.49, 0.53, 0),
            asset("KXETH15M", 0.52, 0.49, 0.55, 1),
        )
        choice = choose_single(
            pair,
            {"KXBTC15M": 0.50, "KXETH15M": 0.50},
            "residual_model",
            0.0,
        )
        self.assertIsNotNone(choice)
        self.assertLess(choice.predicted_ev, 0)

    def test_exact_fee_and_settlement_pnl(self):
        state = asset("KXBTC15M", 0.60, 0.59, 0.61, 1)
        _, fill, _, _, won, pnl = candidate(state, "yes", 0.65, 0.0)
        self.assertTrue(won)
        self.assertAlmostEqual(fill, 0.61)
        self.assertAlmostEqual(pnl, 1.0 - 0.61 - fee(0.61))

    def test_offset_model_learns_without_replacing_market_prior(self):
        features = np.zeros((20, 2))
        offsets = np.array([0.0] * 20)
        outcomes = np.array([1] * 15 + [0] * 5)
        model = fit_offset_model(features, offsets, outcomes, ridge=10.0)
        prediction = model.predict(np.zeros((1, 2)), np.array([0.0]))[0]
        self.assertGreater(prediction, 0.5)
        self.assertLess(prediction, 0.9)


if __name__ == "__main__":
    unittest.main()
