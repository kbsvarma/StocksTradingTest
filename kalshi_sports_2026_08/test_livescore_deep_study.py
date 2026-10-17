import unittest

import livescore_deep_study as study
import screen_states


class ScoreStateTests(unittest.TestCase):
    def row(self, games_for, games_against, server=1):
        return {
            "sets": [1, 0],
            "games": [[games_for], [games_against]],
            "points": ["0", "0"],
            "server": server,
            "is_tiebreak": False,
        }

    def test_serving_for_match_accepts_real_pre_game_scores(self):
        states = screen_states.states_for(self.row(5, 4))
        self.assertEqual(states["P2_serving_for_match"], 0)
        states = screen_states.states_for(self.row(6, 5))
        self.assertEqual(states["P2_serving_for_match"], 0)

    def test_serving_for_match_rejects_terminal_set_rows(self):
        self.assertNotIn(
            "P2_serving_for_match", screen_states.states_for(self.row(6, 0))
        )
        self.assertNotIn(
            "P2_serving_for_match", screen_states.states_for(self.row(7, 5))
        )


class StatisticsTests(unittest.TestCase):
    def test_student_t_tail_at_zero(self):
        self.assertAlmostEqual(study.student_t_upper_tail(0.0, 30), 0.5, places=10)

    def test_student_t_known_five_percent_point(self):
        self.assertAlmostEqual(
            study.student_t_upper_tail(1.697, 30), 0.05, delta=0.001
        )


if __name__ == "__main__":
    unittest.main()
