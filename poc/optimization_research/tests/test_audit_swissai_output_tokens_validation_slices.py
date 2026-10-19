import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import audit_swissai_output_tokens_validation_slices as supplement


class SwissaiOutputTokenValidationSliceTests(unittest.TestCase):
    def test_frozen_validation_hash_manifest_and_gate_match(self):
        validation = supplement.verify_frozen_validation()
        self.assertFalse(validation['gate']['passes'])
        self.assertEqual(validation['rows'], 212_296)

    def test_slice_scores_separate_known_and_unseen_without_test_rows(self):
        first = supplement.frozen.day_number(supplement.frozen.VALID_BEGIN)
        data = {
            'codes': np.array([1, 2, 1, 2, 3], dtype=np.int32),
            'day': np.array([first, first, first + 1, first + 1,
                             supplement.frozen.day_number(supplement.frozen.TEST_BEGIN)]),
            'y': np.array([10., 20., 30., 40., 9999.]),
        }
        indices = np.array([0, 1, 2, 3])
        predictions = {
            name: np.array([15., 25., 35., 45.])
            for name in supplement.frozen.METHODS
        }
        with patch.object(supplement.frozen, 'split_indices', return_value=indices):
            scores = supplement.score_slices(
                data, indices, predictions, np.array([1], dtype=np.int32),
            )
            self.assertEqual(scores['all']['rows'], 4)
            self.assertEqual(scores['sampled_training_model']['rows'], 2)
            self.assertEqual(scores['unseen_training_model']['rows'], 2)
            self.assertEqual(scores['sampled_training_model']['models'], 1)
            self.assertEqual(scores['unseen_training_model']['models'], 1)
            self.assertEqual(scores['all']['scores']['hgb']['overall']['count'], 4)
            with self.assertRaisesRegex(ValueError, 'full_validation_only'):
                supplement.score_slices(
                    data, np.array([0, 1, 2, 3, 4]),
                    {name: np.array([15., 25., 35., 45., 9999.])
                     for name in supplement.frozen.METHODS},
                    np.array([1], dtype=np.int32),
                )

    def test_changed_validation_hash_is_rejected(self):
        with patch.object(supplement, 'EXPECTED_VALIDATION_SHA256', '0' * 64):
            with self.assertRaisesRegex(ValueError, 'frozen_validation_score_changed'):
                supplement.verify_frozen_validation()


if __name__ == '__main__':
    unittest.main()
