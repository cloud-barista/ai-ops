"""Integration checks for version isolation and complete repeated-cell panels."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from audit_repeat_variability import live_cells, later_campaign, paired_scaling


ROOT = Path(__file__).resolve().parents[1]


class RepeatVariabilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.frame = pd.read_parquet(ROOT / 'data/audit_v1/measurements.parquet')

    def test_exact_v2_panel_and_later_overlap(self):
        live, cells = live_cells(self.frame)
        self.assertEqual((live.run_id.nunique(), len(cells)), (47, 16))
        self.assertEqual(len(paired_scaling(live)), 6)
        later = later_campaign(self.frame, cells)
        self.assertEqual(len(later), 16)
        self.assertTrue((later.later_repetitions == 2).all())

    def test_v1_outcomes_cannot_affect_v2_statistics(self):
        _, original = live_cells(self.frame)
        changed = self.frame.copy()
        mask = ((changed.campaign == 'khu-hf-live') &
                (changed.collector_version == 'khu-hf-direct-collector/v1'))
        changed.loc[mask, 'latency_p95_ms'] = 1e12
        changed.loc[mask, 'examples_per_second'] = 1e-12
        _, after = live_cells(changed)
        pd.testing.assert_frame_equal(original, after)

    def test_missing_measurement_rejected(self):
        row = self.frame[(self.frame.campaign == 'khu-hf-live') &
                         (self.frame.collector_version ==
                          'khu-hf-direct-collector/v2') &
                         (self.frame.device == 'cuda')].index[0]
        with self.assertRaises(ValueError):
            live_cells(self.frame.drop(index=row))


if __name__ == '__main__':
    unittest.main()
