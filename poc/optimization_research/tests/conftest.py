"""Test-only isolation for frozen experiments."""

import pytest


@pytest.fixture(autouse=True)
def isolate_burstgpt_freeze_prediction_path(request):
    # The frozen hash-guard test redirects every output except this path.
    # Redirect it here without changing the already scored script or test bytes.
    if (request.node.path.name != "test_burstgpt_input_demand.py"
            or request.node.name != "test_freeze_hash_and_version_guard"):
        return
    temp_dir = request.getfixturevalue("tmp_path")
    monkeypatch = request.getfixturevalue("monkeypatch")
    monkeypatch.setattr(request.module.demand, "PREDICTIONS",
                        temp_dir / "validation_predictions.csv")
