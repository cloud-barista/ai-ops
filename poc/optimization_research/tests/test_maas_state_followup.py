"""Synthetic timing, cohort, and missingness tests for the MaaS follow-up."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import unittest

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))
SPEC = importlib.util.spec_from_file_location(
    "evaluate_maas_state_followup", SCRIPTS / "evaluate_maas_state_followup.py")
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def case_rows(letter: str, center: int, *, middle: bool = True, oldest: bool = True,
              future_count: int = 6, old_mem: float = 10.0, duplicate_current: bool = False) -> list[dict]:
    times = ([center - 600] if oldest else []) + ([center - 300] if middle else []) + [center]
    times += [center + offset for offset in MODULE.original.FUTURE_OFFSETS[:future_count]]
    if duplicate_current:
        times += [center]
    rows = []
    for time in times:
        rows.append({"instance_hash": letter * 64, "time_s": time, "cluster_id": "c1",
                     "gpu_count": 1.0, "sm_util": 81.0 if time == center + 600 else 10.0,
                     "gpu_mem_util": old_mem if time == center - 600 else 10.0,
                     "samples": 15.0, "alarm_tie_hash": "0" * 64})
    return rows


class MaaSStateFollowupTests(unittest.TestCase):
    def test_complete_partial_and_unobserved_future_are_separate(self) -> None:
        center = 140 * 86400 + 3600
        raw = pd.DataFrame(
            case_rows("a", center) + case_rows("b", center, middle=False) +
            case_rows("c", center, old_mem=101.0) +
            case_rows("d", center, middle=False, future_count=5)
        )
        examples, audit = MODULE.construct_cohorts(raw)
        chosen = examples.loc[examples.time_s.eq(center)]
        self.assertEqual(set(zip(chosen.instance_hash.str[0], chosen.cohort)),
                         {("a", "complete"), ("b", "partial_past"), ("c", "partial_past")})
        self.assertTrue(chosen.y.eq(1).all())
        by_letter = {row.instance_hash[0]: row for row in chosen.itertuples()}
        self.assertEqual(by_letter["b"].past_age_s, 600)
        self.assertEqual(by_letter["c"].past_age_s, 300)
        self.assertTrue(np.isnan(by_letter["c"].mem_oldest))
        partial_audit = audit["splits"]["train"]["cohorts"]["partial_past"]
        self.assertGreaterEqual(partial_audit["candidates"] - partial_audit["labeled"], 1)
        self.assertGreaterEqual(partial_audit["reasons_nonexclusive"]["future_missing"], 1)

    def test_duplicate_current_counts_as_one_insufficient_decision(self) -> None:
        center = 140 * 86400 + 3600
        raw = pd.DataFrame(case_rows("a", center) + case_rows("e", center, duplicate_current=True))
        examples, audit = MODULE.construct_cohorts(raw)
        self.assertFalse((examples.instance_hash.eq("e" * 64) & examples.time_s.eq(center)).any())
        self.assertEqual(audit["splits"]["train"]["current_insufficient_evidence_keys"], 1)

    def test_label_delay_excludes_row_that_crosses_train_boundary(self) -> None:
        train_end = MODULE.original.SPLITS["train"][1]
        center = train_end - MODULE.original.DECISION_DELAY_S - 2100 + 300
        raw = pd.DataFrame(case_rows("a", 140 * 86400 + 3600) + case_rows("f", center))
        examples, _ = MODULE.construct_cohorts(raw)
        self.assertFalse((examples.time_s == center).any())

    def test_imputation_uses_train_only_and_preserves_current_meter(self) -> None:
        base = {"sm_middle": 20.0, "sm_current": 30.0,
                "mem_middle": 40.0, "mem_current": 50.0,
                "samples_middle": 10.0, "samples_current": 10.0,
                "gpu_count": 1.0, "current_high": 0, "any_recent_high": 0,
                "valid_oldest": 1, "valid_middle": 1, "past_age_s": 300}
        train = pd.DataFrame([{**base, "sm_oldest": 4.0, "mem_oldest": 6.0,
                               "samples_oldest": 8.0}])
        validation = pd.DataFrame([{**base, "sm_oldest": np.nan, "mem_oldest": np.nan,
                                    "samples_oldest": np.nan, "sm_current": 70.0,
                                    "valid_oldest": 0}])
        median = MODULE.train_imputation(train)
        self.assertEqual(median["sm_oldest"], 4.0)
        made = MODULE.make_features(validation, median).iloc[0]
        self.assertEqual(made.sm_oldest, 4.0)
        self.assertEqual(made.mem_oldest, 6.0)
        self.assertEqual(made.samples_oldest, 8.0)
        self.assertEqual(made.sm_current, 70.0)
        self.assertAlmostEqual(made.sm_mean3, (4 + 20 + 70) / 3)
        self.assertEqual(made.sm_change3, 66.0)
        self.assertEqual(made.valid_oldest, 0)

    def test_recent_high_uses_only_valid_observed_past_and_age_is_causal(self) -> None:
        center = 140 * 86400 + 3600
        raw = pd.DataFrame(case_rows("h", center, middle=False, old_mem=95.0) +
                           case_rows("i", center, middle=False, old_mem=101.0))
        examples, _ = MODULE.construct_cohorts(raw)
        at_center = {row.instance_hash[0]: row for row in
                     examples.loc[examples.time_s.eq(center)].itertuples()}
        self.assertEqual((at_center["h"].current_high, at_center["h"].any_recent_high,
                          at_center["h"].past_age_s), (0, 1, 600))
        self.assertEqual((at_center["i"].current_high, at_center["i"].any_recent_high,
                          at_center["i"].past_age_s), (0, 0, 900))

    def test_state_class_gate_checks_both_train_and_validation_states(self) -> None:
        rows = pd.DataFrame([
            {"split": split, "current_high": state, "y": y}
            for split in ("train", "validation")
            for state in (0, 1) for y in (0, 1)
        ])
        self.assertTrue(MODULE.state_model_class_gate(rows)["passes"])
        missing_high_negative = rows.loc[~(
            rows.split.eq("validation") & rows.current_high.eq(1) & rows.y.eq(0))]
        gate = MODULE.state_model_class_gate(missing_high_negative)
        self.assertFalse(gate["passes"])
        self.assertFalse(gate["by_state"]["high"]["validation"]["both_classes"])


if __name__ == "__main__":
    unittest.main()
