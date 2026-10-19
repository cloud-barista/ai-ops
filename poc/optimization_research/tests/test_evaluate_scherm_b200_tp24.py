"""Guard the held-out-model boundary in the Scherm TP response study."""

from __future__ import annotations

import hashlib
import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import evaluate_scherm_b200_tp24 as study


class SchermB200TP24Tests(unittest.TestCase):
    def test_pinned_source_and_declared_pairs(self):
        self.assertEqual(hashlib.sha256(study.SOURCE.read_bytes()).hexdigest(),
                         study.SOURCE_SHA256)
        pairs = study.source_pairs()
        self.assertEqual((len(pairs), pairs.model.nunique()), (64, 7))
        self.assertFalse(pairs[["model", "concurrency"]].duplicated().any())
        self.assertTrue(pairs[["n_ok_tp2", "n_ok_tp4"]].ge(10).all().all())

    def test_held_out_tp4_labels_do_not_change_its_forecasts(self):
        pairs = study.source_pairs()
        held = sorted(pairs.model.unique())[0]
        original, _ = study.evaluate(pairs)
        changed = pairs.copy(deep=True)
        affected = changed.model.eq(held)
        for target in study.TARGETS:
            changed.loc[affected, f"{target}_tp4"] *= 7
            changed.loc[affected, f"log_ratio_{target}"] += np.log(7)
        replay, _ = study.evaluate(changed)
        keys = ["held_out_model", "concurrency", "target", "method"]
        a = original.loc[original.held_out_model.eq(held)].sort_values(keys)
        b = replay.loc[replay.held_out_model.eq(held)].sort_values(keys)
        np.testing.assert_array_equal(a.predicted_log_ratio.to_numpy(),
                                      b.predicted_log_ratio.to_numpy())
        self.assertFalse(np.array_equal(a.true_log_ratio.to_numpy(),
                                        b.true_log_ratio.to_numpy()))


if __name__ == "__main__":
    unittest.main()
