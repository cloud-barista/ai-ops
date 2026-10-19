"""Post-hoc description of the fixed Azure input-demand transfer failure.

This script reads both *already scored* services. Its output is diagnostic and
must not be used as a new independent model-selection or validation result.
"""

from __future__ import annotations

import json
from pathlib import Path

import joblib
import numpy as np

import evaluate_azure_code_input_demand as coding
import evaluate_azure_input_demand_cross_service as transfer_v1
import evaluate_azure_input_demand_cross_service_v1_1 as transfer_v1_1


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "artifacts/azure_input_demand_transfer_shift_diagnosis_v1/summary.json"


def describe(frame, selected: slice, bundle: dict) -> dict:
    part = frame.iloc[selected]
    mask = (part["scale"] >= coding.MIN_SCALE).to_numpy()
    if not mask.any():
        raise ValueError("no eligible windows")
    ratios = part["ratio"].to_numpy(float)[mask]
    scales = part["scale"].to_numpy(float)[mask]
    targets = part["y"].to_numpy(float)[mask]
    estimates = coding.predict_from_bundle(bundle, frame)["relative_q90_hgb"][
        selected][mask]
    estimate_ratios = estimates / scales
    return {
        "eligible_windows": int(mask.sum()),
        "observed_ratio_median": float(np.median(ratios)),
        "observed_ratio_q90": float(np.quantile(ratios, 0.9)),
        "observed_ratio_scale_weighted_q90": coding.weighted_quantile(
            ratios, scales, 0.9),
        "observed_ratio_q99": float(np.quantile(ratios, 0.99)),
        "forecast_ratio_median": float(np.median(estimate_ratios)),
        "forecast_ratio_mean": float(np.mean(estimate_ratios)),
        "forecast_coverage": float(np.mean(targets <= estimates)),
    }


def main() -> None:
    coding.verify_source()
    if (transfer_v1.SOURCE.stat().st_size != transfer_v1.SOURCE_BYTES
            or transfer_v1._sha256(transfer_v1.SOURCE)
            != transfer_v1.SOURCE_SHA256):
        raise ValueError("official Conversation source changed")
    coding_y, _, _ = coding.aggregate_source(coding.SOURCE)
    conversation_y, _, _ = transfer_v1_1.aggregate_source_iso(
        transfer_v1.SOURCE)
    coding_frame = coding.design_from_aggregates(coding_y)
    conversation_frame = coding.design_from_aggregates(
        conversation_y, start=transfer_v1.CONV_START)
    bundle = joblib.load(coding.OUTPUT / "relative_q90_model.joblib")
    result = {
        "status": "POST_HOC_DESCRIPTIVE_ONLY",
        "coding_source_sha256": coding.SOURCE_SHA256,
        "conversation_source_sha256": transfer_v1.SOURCE_SHA256,
        "coding_model_sha256": coding.file_sha256(
            coding.OUTPUT / "relative_q90_model.joblib"),
        "conversation_score_sha256": coding.file_sha256(
            transfer_v1_1.RESULT),
        "coding_fit": describe(coding_frame, coding.TRAIN, bundle),
        "coding_retrospective_assessment": describe(
            coding_frame, coding.ASSESSMENT, bundle),
        "conversation_transfer_test": describe(
            conversation_frame, slice(2 * 288, 7 * 288), bundle),
        "interpretation_limit": (
            "The Conversation test was already scored before this analysis. "
            "These summaries cannot validate a method changed in response."
        ),
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    coding.atomic_json(OUTPUT, result)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
