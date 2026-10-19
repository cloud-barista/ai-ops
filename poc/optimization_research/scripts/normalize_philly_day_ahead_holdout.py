"""Normalize the frozen [25,35) Philly sample with the existing width rule."""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import patch

import normalize_philly_gpu_sample as normalizer
import fetch_philly_day_ahead_holdout as collector
import fetch_philly_gpu_sample as source


ROOT = Path(__file__).resolve().parents[1]
RAW = collector.OUTPUT_DIR / collector.OUTPUT_NAME
RAW_AUDIT = collector.OUTPUT_DIR / collector.AUDIT_NAME
NORMALIZED = collector.OUTPUT_DIR / "gpu_util_normalized_first_width.parquet"
AUDIT = collector.OUTPUT_DIR / "gpu_util_normalized_first_width_audit.json"


def normalize() -> dict:
    frozen = collector.verified_freeze()
    if frozen.get("normalizer_wrapper_sha256") != collector.sha256(Path(__file__)):
        raise ValueError("normalizer wrapper changed after freeze")
    if frozen.get("normalizer_sha256") != collector.sha256(Path(normalizer.__file__)):
        raise ValueError("original width normalizer changed after freeze")
    raw = json.loads(RAW_AUDIT.read_text(encoding="utf-8"))
    if (raw.get("complete") is not True or raw.get("integrity_pass") is not True
            or raw.get("pre_fetch_freeze_sha256") != collector.sha256(collector.FREEZE)
            or raw.get("wrapper_collector_sha256") != frozen["collector_sha256"]
            or raw.get("compressed_sha256") != source.EXPECTED_SHA256
            or raw.get("sample_rule") != collector.SELECTION_RULE
            or raw.get("sample_sha256") != collector.sha256(RAW)):
        raise ValueError("raw [25,35) collection is not verified")
    with patch.object(normalizer, "MIN_FREE_BYTES", collector.MIN_FREE_BYTES):
        result = normalizer.normalize_sample(RAW, RAW_AUDIT, NORMALIZED, AUDIT)
    if (result["rows"] != raw["gpu_csv"]["selected_rows"]
            or result["source_row_index_sequence_sha256"]
            != result["derived_row_index_sequence_sha256"]):
        raise ValueError("normalized [25,35) source row sequence changed")
    # An audit-only field records the wrapper's smaller, explicit disk guard.
    result["normalizer_wrapper_sha256"] = frozen["normalizer_wrapper_sha256"]
    result["minimum_free_bytes"] = collector.MIN_FREE_BYTES
    temp = AUDIT.with_name(AUDIT.name + ".wrapper.tmp")
    temp.write_text(json.dumps(result, indent=2, sort_keys=True)+"\n")
    os.replace(temp, AUDIT)
    return result


if __name__ == "__main__":
    result = normalize()
    print(json.dumps({"rows": result["rows"],
                      "invalid_rows": result["invalid_rows"],
                      "output_sha256": result["output_sha256"]}, sort_keys=True))
