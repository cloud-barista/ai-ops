"""Causal-history and split checks for the exposed e/g start-delay study."""
from __future__ import annotations

import sys
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import evaluate_google_2019_job_schedule_5s as study


def rows(times: list[int], labels: list[int], cells: list[str] | None = None) -> pd.DataFrame:
    if cells is None:
        cells = ["e"] * len(times)
    return pd.DataFrame({
        "cell": cells,
        "job_key": [f"{i:064x}" for i in range(len(times))],
        "submit_time_us": times,
        "scheduling_class": [0] * len(times),
        "priority": [10] * len(times),
        "scheduler": [0] * len(times),
        "has_alloc_collection": [0] * len(times),
        "has_parent_collection": [0] * len(times),
        "start_after_count": [0] * len(times),
        "late_schedule_5s": labels,
    }).sort_values(["cell", "submit_time_us", "job_key"]).reset_index(drop=True)


class JobScheduleEvaluationTests(unittest.TestCase):
    def test_history_excludes_current_job_and_immature_five_second_label(self):
        frame = rows([1_000_000, 6_000_000, 6_000_001, 7_000_000], [1, 0, 0, 0])
        history = study.causal_history(frame)
        self.assertEqual(history.cell_count.tolist(), [0, 0, 1, 1])
        self.assertEqual(history.cell_late.tolist(), [0, 0, 1, 1])
        self.assertEqual(history.group_count.tolist(), [0, 0, 1, 1])
        self.assertEqual(history.full_count.tolist(), [0, 0, 1, 1])

    def test_history_is_cell_and_group_local_and_uses_last_hour(self):
        frame = rows([1, 50_000_000, study.HOUR_US + 3,
                      study.HOUR_US + 40_000_000, 1, study.HOUR_US + 40_000_000],
                     [1, 0, 0, 1, 1, 0], ["e", "e", "e", "e", "g", "g"])
        frame.loc[frame.cell.eq("e") & frame.submit_time_us.eq(50_000_000), "priority"] = 11
        history = study.causal_history(frame)
        e_last = frame.index[(frame.cell == "e") &
                             (frame.submit_time_us == study.HOUR_US + 40_000_000)][0]
        g_last = frame.index[(frame.cell == "g") &
                             (frame.submit_time_us == study.HOUR_US + 40_000_000)][0]
        self.assertEqual(int(history.loc[e_last, "cell_count"]), 2)
        self.assertEqual(int(history.loc[e_last, "cell_late"]), 0)
        self.assertEqual(int(history.loc[e_last, "group_count"]), 1)
        self.assertEqual(int(history.loc[g_last, "cell_count"]), 0)

    def test_temporal_split_embargoes_unmatured_boundary_labels(self):
        day = study.DAY_US
        frame = rows([study.HOUR_US - 1, study.HOUR_US, 20 * day - 6_000_000,
                      20 * day - 5_000_000, 20 * day,
                      25 * day - 5_000_000, 25 * day], [0] * 7)
        masks = study.split_masks(frame)
        self.assertEqual(masks["train"].tolist(),
                         [False, True, True, False, False, False, False])
        self.assertEqual(masks["validation"].tolist(),
                         [False, False, False, False, True, False, False])
        self.assertEqual(masks["development_stress"].tolist(),
                         [False, False, False, False, False, False, True])

    def test_exposed_cells_only_and_top_tenth_metric(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            e_dir = root / "e"
            f_dir = root / "f"
            e_dir.mkdir()
            f_dir.mkdir()
            for cell, folder in (("e", e_dir), ("f", f_dir)):
                rows([1], [0], [cell]).to_csv(folder / "jobs.csv", index=False)
                (folder / "metadata.json").write_text(json.dumps({
                    "cell": cell, "csv_sha256": study.sha256(folder / "jobs.csv"),
                    "builder_sha256": study.sha256(Path(study.builder.__file__)),
                    "source": {"sha256": study.builder.PINNED_SHA256[cell]}
                    if cell in study.builder.PINNED_SHA256 else {},
                }))
            with patch.object(study.builder, "verify_official_input",
                              return_value={"source_bytes": 1}):
                # Cell e is structurally valid through provenance preflight.
                metadata_path = e_dir / "metadata.json"
                metadata = json.loads(metadata_path.read_text())
                metadata["source"]["input_path"] = "unused-in-mock.gz"
                metadata["source"]["source_bytes"] = 1
                metadata["source"]["bytes"] = 1
                metadata["audit"] = {"labeled_jobs": 1, "label_0_jobs": 1}
                metadata_path.write_text(json.dumps(metadata))
                with self.assertRaisesRegex(ValueError, "exposed_e_and_g"):
                    study.read_jobs(e_dir, f_dir)
        values = study.scored(np.asarray([0, 1, 1, 0]),
                              np.asarray([.1, .9, .2, .1]),
                              np.asarray(["a", "b", "c", "d"]))
        self.assertEqual(values["top_10pct_alerts"], 1)
        self.assertAlmostEqual(values["top_10pct_recall"], .5)
        self.assertAlmostEqual(values["top_10pct_precision"], 1)

    def test_unpinned_builder_or_source_is_rejected_before_scoring(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            folders = {}
            for cell in ("e", "g"):
                folder = root / cell
                folder.mkdir()
                rows([1], [0], [cell]).to_csv(folder / "jobs.csv", index=False)
                folders[cell] = folder
                (folder / "metadata.json").write_text(json.dumps({
                    "cell": cell,
                    "csv_sha256": study.sha256(folder / "jobs.csv"),
                    "builder_sha256": "0" * 64,
                }))
            with self.assertRaisesRegex(ValueError, "builder_code_sha256_mismatch"):
                study.read_jobs(folders["e"], folders["g"])
            for cell in ("e", "g"):
                folder = folders[cell]
                (folder / "metadata.json").write_text(json.dumps({
                    "cell": cell,
                    "csv_sha256": study.sha256(folder / "jobs.csv"),
                    "builder_sha256": study.sha256(Path(study.builder.__file__)),
                    "source": {"sha256": "0" * 64},
                }))
            with patch.object(study.builder, "verify_official_input",
                              side_effect=AssertionError("must not open source")):
                with self.assertRaisesRegex(ValueError, "pinned_source_sha256_mismatch"):
                    study.read_jobs(folders["e"], folders["g"])

    def test_day_block_interval_resamples_days_not_jobs(self):
        actual = np.asarray([1, 1, 0, 0])
        model = np.asarray([.8, .8, .2, .2])
        baseline = np.asarray([.5] * 4)
        days = np.asarray([20, 20, 21, 21])
        ci = study.day_block_brier_difference_ci(actual, model, baseline, days,
                                                 repeats=100, seed=42)
        self.assertEqual(ci["days"], 2)
        self.assertAlmostEqual(ci["lower_95"], .21)
        self.assertAlmostEqual(ci["upper_95"], .21)
        single = study.day_block_brier_difference_ci(actual, model, baseline,
                                                      np.asarray([20] * 4))
        self.assertIsNone(single["lower_95"])

    def test_pooled_static_prior_uses_training_labels_only(self):
        day = study.DAY_US
        frame = rows([day, day, 21 * day, 21 * day], [1, 0, 0, 1],
                     ["e", "g", "e", "g"])
        train = frame.submit_time_us.to_numpy() < 20 * day
        prior = {"e": 1.0, "g": 0.0}
        initial = study.static_lookup(frame, train, prior, study.FULL_GROUP,
                                      10.0, pooled=True)
        changed = frame.copy()
        changed.loc[~train, "late_schedule_5s"] = 1
        after = study.static_lookup(changed, train, prior, study.FULL_GROUP,
                                    10.0, pooled=True)
        np.testing.assert_allclose(initial, after)
        np.testing.assert_allclose(initial, [.5, .5, .5, .5])


if __name__ == "__main__":
    unittest.main()
