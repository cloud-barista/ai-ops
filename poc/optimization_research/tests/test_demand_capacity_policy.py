import unittest
import numpy as np
from demand_capacity_policy import Config, State, decide, replay, feedback_correct


class PolicyTests(unittest.TestCase):
    def test_ready_delay_and_no_duplicate_out(self):
        rows = replay([300]*5, [300]*5, Config(100, startup_bins=2), 1)
        self.assertEqual([r['active_slots'] for r in rows[:3]], [1, 1, 2])
        self.assertEqual(rows[0]['pending_ready_bin'], 2)
        self.assertEqual(rows[1]['reason'], 'capacity_pending')
        self.assertEqual(rows[0]['requested_slots'], 2)

    def test_low_confirmations_and_cooldown(self):
        rows = replay([0]*6, [0]*6, Config(100), 3)
        self.assertEqual([r['action'] for r in rows], ['KEEP','KEEP','SCALE_IN','KEEP','KEEP','SCALE_IN'])
        self.assertEqual(rows[-1]['active_slots'], 1)

    def test_max_bound(self):
        rows = replay([1000]*12, [1000]*12, Config(100, max_slots=2), 2)
        self.assertTrue(all(r['requested_slots'] == 2 for r in rows))

    def test_abstain_does_not_mean_zero(self):
        rows = replay([100], [np.nan], Config(100), 2)
        self.assertEqual(rows[0]['action'], 'ABSTAIN')
        self.assertEqual(rows[0]['active_slots'], 2)

    def test_future_outcome_not_in_decision(self):
        a = replay([90, 100, 500], [80, 80, 80], Config(100), 2)
        b = replay([90, 100, 1], [80, 80, 80], Config(100), 2)
        for k in ('action','reason','slots_before','active_slots','pending_ready_bin','forecast_tokens'):
            self.assertEqual([r[k] for r in a], [r[k] for r in b])

    def test_fluid_conservation(self):
        rows = replay([250, 0, 0], [0, 0, 0], Config(100), 1, fixed=True)
        self.assertEqual([r['backlog_tokens'] for r in rows], [150, 50, 0])

    def test_backlog_prevents_shrink(self):
        state = State(2, backlog_tokens=250)
        self.assertEqual(decide(state, 0, 0, Config(100))[0], 'SCALE_OUT')

    def test_feedback_uses_only_completed_bins(self):
        y = np.arange(400.) + 1000
        p, m = np.full(400, 1000.), np.full(400, 1000.)
        a = feedback_correct(y, p, m, 288)
        y[300:] = 1e9
        b = feedback_correct(y, p, m, 288)
        np.testing.assert_array_equal(a[:301], b[:301])
        self.assertNotEqual(a[340], b[340])

    def test_feedback_unsupported_history(self):
        p = np.full(400, 100.)
        p[0:288] = np.nan
        a = feedback_correct(np.ones(400), p, np.full(400,1000.), 288)
        self.assertEqual(a[288], 100.)

    def test_invalid_inputs(self):
        for v in (0, np.nan, -1):
            with self.assertRaises(ValueError): Config(v)
        with self.assertRaises(ValueError): Config(1, startup_bins=0)
        with self.assertRaises(ValueError): replay([-1], [1], Config(1), 1)


if __name__ == '__main__':
    unittest.main()
