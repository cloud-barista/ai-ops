"""The repair changes timestamp decoding only, before any target score."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import evaluate_ragpulse_input_demand_transfer as original
import evaluate_ragpulse_input_demand_transfer_v1_1 as repair


class RAGPulseParserRepairTests(unittest.TestCase):
    def test_string_seconds_preserve_subsecond_bins_duplicates_and_zero(self):
        rows = [
            {"timestamp": "0", "input_length": 10},
            {"timestamp": "299.999", "input_length": 20},
            {"timestamp": "300", "input_length": 0},
            {"timestamp": "300", "input_length": 15},
        ]
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "trace.jsonl"
            source.write_text("\n".join(json.dumps(row) for row in rows)+"\n")
            with patch.object(original, "EXPECTED_ROWS", len(rows)):
                y, counts, nrows = repair.aggregate_jsonl(source, 4)
            self.assertEqual(nrows, 4)
            self.assertEqual(y.tolist(), [30, 15, 0, 0])
            self.assertEqual(counts.tolist(), [2, 2, 0, 0])

    def test_numeric_inputs_match_original_parser(self):
        rows = [{"timestamp": 0, "input_length": 10},
                {"timestamp": 299.9, "input_length": 20},
                {"timestamp": 300, "input_length": 15}]
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "trace.jsonl"
            source.write_text("\n".join(json.dumps(row) for row in rows)+"\n")
            with patch.object(original, "EXPECTED_ROWS", len(rows)):
                before = original.aggregate_jsonl(source, 4)
                after = repair.aggregate_jsonl(source, 4)
            self.assertEqual(before[0].tolist(), after[0].tolist())
            self.assertEqual(before[1].tolist(), after[1].tolist())
            self.assertEqual(before[2], after[2])

    def test_invalid_strings_still_fail(self):
        for stamp in ("NaN", "-1", " 1", "2024-01-01", "1200", "299.0junk"):
            with self.subTest(stamp=stamp), tempfile.TemporaryDirectory() as folder:
                source = Path(folder) / "trace.jsonl"
                source.write_text(json.dumps({"timestamp": stamp, "input_length": 1})+"\n")
                with patch.object(original, "EXPECTED_ROWS", 1):
                    with self.assertRaises(ValueError):
                        repair.aggregate_jsonl(source, 4)


if __name__ == "__main__":
    unittest.main()
