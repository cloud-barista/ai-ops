import unittest
import numpy as np
from copilot_call_feedback import correct_completed


def peaks(y, h):
    return np.r_[np.max(np.lib.stride_tricks.sliding_window_view(y, h + 1), axis=1),
                 np.full(h, np.nan)]


class FeedbackTests(unittest.TestCase):
    def test_label_maturity_and_extra_delivery_lag(self):
        for lag in (0, 1, 6):
            y = np.ones(500)
            changed = y.copy(); changed[250] = 100
            p = np.ones(500); m = p.copy()
            opts = dict(first_origin=100, minimum=1, window=1, delivery_lag=lag)
            a = correct_completed(peaks(y, 6), p, m, 6, **opts)[0]
            b = correct_completed(peaks(changed, 6), p, m, 6, **opts)[0]
            np.testing.assert_array_equal(a[:251 + lag], b[:251 + lag])
            self.assertGreater(b[251 + lag], a[251 + lag])

    def test_fitted_training_rows_never_used(self):
        y = np.full(1100, 100.)
        p = np.ones(1100); m = p.copy()
        a = correct_completed(y, p, m, 6)
        self.assertEqual(a[0][864 + 6 + 95], 1.)
        self.assertGreater(a[0][864 + 6 + 96], 1.)
        self.assertTrue(np.all(a[3][a[3] >= 0] <= np.arange(len(p))[a[3] >= 0]))

    def test_call_unit_and_minimum_support(self):
        y = np.full(20, 5.); p = np.ones(20); m = np.ones(20)
        a = correct_completed(y, p, m, 1, first_origin=0, minimum=2, window=3)
        self.assertEqual(a[0][2], 1.)
        self.assertEqual(a[0][3], 5.)
        m[3] = 0
        self.assertEqual(correct_completed(y, p, m, 1, first_origin=0, minimum=2, window=3)[0][3], 1.)

    def test_recent_window_and_missing_feedback(self):
        y = np.r_[np.full(10, 100.), np.ones(20)]
        p = np.ones(30); m = p.copy()
        a = correct_completed(y, p, m, 1, first_origin=0, minimum=2, window=3)
        self.assertEqual(a[0][20], 1.)
        y[17:20] = np.nan
        b = correct_completed(y, p, m, 1, first_origin=0, minimum=3, window=3)
        self.assertEqual(b[0][21], 1.)

    def test_future_mutation_and_no_recursive_residuals(self):
        y = np.arange(400, dtype=float) + 1
        p = np.ones(400); m = p.copy()
        a = correct_completed(peaks(y, 2), p, m, 2, first_origin=0)
        y[250:] = 1e6
        b = correct_completed(peaks(y, 2), p, m, 2, first_origin=0)
        np.testing.assert_array_equal(a[0][:251], b[0][:251])
        const = correct_completed(np.full(200, 5.), np.ones(200), np.ones(200),
                                  1, first_origin=0, minimum=2, window=3)
        np.testing.assert_equal(const[0][3:], 5.)

    def test_negative_correction_clipped_and_caller_unchanged(self):
        y = np.ones(30); p = np.full(30, 5.); m = y.copy()
        before = p.copy()
        out = correct_completed(y, p, m, 1, first_origin=0, minimum=2, window=3)
        np.testing.assert_array_equal(out[0], before)
        np.testing.assert_array_equal(p, before)

    def test_invalid_inputs(self):
        for args in ({'horizon':True}, {'horizon':-1}, {'horizon':5},
                     {'horizon':1,'window':0}, {'horizon':1,'minimum':4,'window':3},
                     {'horizon':1,'delivery_lag':-1}, {'horizon':1,'first_origin':True}):
            with self.assertRaises(ValueError):
                correct_completed(np.ones(5),np.ones(5),np.ones(5),**args)
        with self.assertRaises(ValueError):
            correct_completed(np.ones(3),np.ones(4),np.ones(3),1)


if __name__ == '__main__':
    unittest.main()
