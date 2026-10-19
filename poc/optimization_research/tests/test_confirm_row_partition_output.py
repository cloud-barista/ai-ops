"""A rerun must add provenance to its requested output, preserving old results."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from confirm_row_partition_forest import output_root


class ConfirmationOutputTests(unittest.TestCase):
    def test_new_output_directory_is_preserved(self):
        self.assertEqual(output_root(['--output', '/tmp/new_inference_confirmation']),
                         Path('/tmp/new_inference_confirmation'))

    def test_default_and_unknown_args(self):
        default = Path('/home/researcher/ai-mcmp/research/artifacts/row_partition_forest_confirmation_v1')
        self.assertEqual(output_root([]), default)
        self.assertEqual(output_root(['--mode', 'row2']), default)


if __name__ == '__main__':
    unittest.main()
