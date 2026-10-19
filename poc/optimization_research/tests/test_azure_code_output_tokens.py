from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

import numpy as np
import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/evaluate_azure_code_output_tokens.py"
SPEC = importlib.util.spec_from_file_location("evaluate_azure_code_output_tokens", SCRIPT)
module = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = module
SPEC.loader.exec_module(module)


def test_histogram_p90_matches_higher_quantile_with_ties():
    values = np.array([0, 0, 1, 1, 1, 5, 8, 8, 100, 100], dtype=np.int64)
    assert module.hist_p90(module.count_hist(values)) == np.quantile(
        values, 0.9, method="higher"
    )


class _ZeroModel:
    def predict(self, x):
        return np.zeros(len(x), dtype=float)


def test_day_history_excludes_unavailable_feedback():
    history = {day: [{day: 10}] for day in range(6)}
    manifest = {
        "bin_edges_context_tokens": [],
        "static_bin_p90": [2],
        "static_global_p90": 2,
    }
    context = np.array([1], dtype=np.int64)
    actual = np.array([2], dtype=np.int64)
    assert module.score_day(4, context, actual, manifest, history, _ZeroModel())[
        "history_days"
    ] == [0, 1, 2]
    assert module.score_day(5, context, actual, manifest, history, _ZeroModel())[
        "history_days"
    ] == [0, 1, 2, 3]
    day6 = module.score_day(6, context, actual, manifest, history, _ZeroModel())
    assert day6["history_days"] == [0, 1, 2, 3, 4]
    assert day6["methods"]["rolling_global"]["mean_budget_tokens"] == 4


def test_day_four_feedback_changes_day_six_but_not_day_five():
    baseline = {day: [{10: 10}] for day in range(5)}
    shifted = {**baseline, 4: [{1000: 10}]}
    manifest = {
        "bin_edges_context_tokens": [],
        "static_bin_p90": [10],
        "static_global_p90": 10,
    }
    context = np.array([1], dtype=np.int64)
    actual = np.array([10], dtype=np.int64)
    day5_baseline = module.score_day(5, context, actual, manifest, baseline, _ZeroModel())
    day5_shifted = module.score_day(5, context, actual, manifest, shifted, _ZeroModel())
    assert day5_baseline["methods"]["rolling_global"] == day5_shifted["methods"]["rolling_global"]
    day6_baseline = module.score_day(6, context, actual, manifest, baseline, _ZeroModel())
    day6_shifted = module.score_day(6, context, actual, manifest, shifted, _ZeroModel())
    assert day6_baseline["methods"]["rolling_global"]["mean_budget_tokens"] == 10
    assert day6_shifted["methods"]["rolling_global"]["mean_budget_tokens"] == 1000


def _prepare_toy_with_invalid_heldout(tmp_path, monkeypatch):
    source = tmp_path / "AzureLLMInferenceTrace_code_1week.csv"
    lines = ["TIMESTAMP,ContextTokens,GeneratedTokens"]
    for day in range(7):
        for row in range(5):
            timestamp = f"2024-05-{10 + day:02d} 00:00:{row:02d}.000000+00:00"
            output = str(day + row) if day < 4 else "invalid-heldout-label"
            lines.append(f"{timestamp},{row + 1},{output}")
    source.write_text("\n".join(lines) + "\n")
    protocol = tmp_path / "protocol.md"
    protocol.write_text("toy fixed protocol\n")
    monkeypatch.setattr(module, "SOURCE", source)
    monkeypatch.setattr(module, "PROTOCOL", protocol)
    monkeypatch.setattr(module, "OUTPUT", tmp_path / "artifacts")
    monkeypatch.setattr(module, "OFFICIAL_BYTES", source.stat().st_size)
    monkeypatch.setattr(module, "OFFICIAL_SHA256", module.sha256(source))
    monkeypatch.setattr(module, "PROTOCOL_SHA256", module.sha256(protocol))
    monkeypatch.setattr(module, "HGB_ROWS_PER_DAY", 5)
    audit = module.audit_inputs()
    assert audit["labels_inspected"] is False
    manifest = module.prepare()
    assert manifest["heldout_labels_inspected"] is False
    return manifest


def test_prepare_does_not_parse_heldout_output(tmp_path, monkeypatch):
    _prepare_toy_with_invalid_heldout(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="held-out GeneratedTokens"):
        module.frozen_test()
    assert not (module.OUTPUT / "heldout_scores.json").exists()


def test_manifest_blocks_changed_code_before_test_labels(tmp_path, monkeypatch):
    _prepare_toy_with_invalid_heldout(tmp_path, monkeypatch)
    original_sha256 = module.sha256

    def changed_code_sha(path: Path) -> str:
        if path.resolve() == Path(module.__file__).resolve():
            return "0" * 64
        return original_sha256(path)

    monkeypatch.setattr(module, "sha256", changed_code_sha)
    with pytest.raises(ValueError, match="script_sha256"):
        module.frozen_test()
    assert not (module.OUTPUT / "heldout_scores.json").exists()
