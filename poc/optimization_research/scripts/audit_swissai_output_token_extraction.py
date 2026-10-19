"""Count SwissAI extraction eligibility without reading output-token values.

This audit only touches label validity masks. It does not compute token
distributions, predictions, or losses, including for the held-out test period.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from extract_swissai_output_tokens import DATA, PARAMETERS, ROOT


PROTOCOL = ROOT / "reports/swissai_output_tokens_protocol.md"
EXPECTED_PROTOCOL_SHA256 = "8de1b6c67aa392ed038b41be97c25845943606bd7c94561f743fc92307ace512"
EXPECTED_SPLITS = {
    "train": {"arrivals": 7_965_677, "output_observed": 6_220_129},
    "validation": {"arrivals": 300_467, "output_observed": 212_296},
    "test": {"arrivals": 736_928, "output_observed": 498_008},
}
SPLIT_BOUNDS = {
    "train": ("2025-05-16", "2025-08-16"),
    "validation": ("2025-08-16", "2025-09-16"),
    "test": ("2025-09-16", "2025-10-24"),
}
DAY_US = 86_400_000_000


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(4 * 1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def utc_us(day: str) -> int:
    return int(datetime.fromisoformat(day).replace(tzinfo=timezone.utc).timestamp() * 1_000_000)


def audit(compact_path: Path, extracted_path: Path) -> dict:
    if sha256(PROTOCOL) != EXPECTED_PROTOCOL_SHA256:
        raise ValueError("SwissAI output-token protocol changed after its freeze")
    compact = pq.read_table(
        compact_path,
        columns=["created_utc_us", "source_row", "status", "model", "elapsed_us"],
    )
    extracted = pq.read_table(extracted_path)
    if compact.num_rows != extracted.num_rows:
        raise ValueError("Compact and extracted Parquet row counts differ")
    compact_ids = compact["source_row"].to_numpy(zero_copy_only=False)
    extracted_ids = extracted["source_row"].to_numpy(zero_copy_only=False)
    if not np.array_equal(compact_ids, extracted_ids):
        raise ValueError("Compact and extracted source_row order differs")
    created = compact["created_utc_us"].to_numpy(zero_copy_only=False).astype(np.int64)
    if np.any(created[1:] < created[:-1]):
        raise ValueError("Compact created_utc_us is not sorted")
    observed = extracted["reported_output_tokens"].is_valid().to_numpy(zero_copy_only=False)
    default = pc.fill_null(pc.equal(compact["status"], "DEFAULT"), False).to_numpy()
    error = pc.fill_null(pc.equal(compact["status"], "ERROR"), False).to_numpy()
    positive_elapsed = pc.fill_null(pc.greater(compact["elapsed_us"], 0), False).to_numpy()
    stripped = pc.utf8_trim_whitespace(compact["model"])
    missing_model = pc.fill_null(
        pc.is_in(stripped, value_set=pa.array(["", "\\N", "<missing>"])), True
    ).to_numpy()
    valid_model = ~missing_model
    eligible = observed & default & positive_elapsed & valid_model
    split_counts = {}
    split_match = True
    for split, (start, end) in SPLIT_BOUNDS.items():
        begin = int(np.searchsorted(created, utc_us(start), side="left"))
        stop = int(np.searchsorted(created, utc_us(end), side="left"))
        observed_here = observed[begin:stop]
        eligible_here = eligible[begin:stop]
        eligible_model = pc.filter(
            compact["model"].slice(begin, stop - begin), pa.array(eligible_here)
        )
        counts = {
            "arrivals": stop - begin,
            "output_observed": int(np.count_nonzero(observed_here)),
            "output_missing": int(np.count_nonzero(~observed_here)),
            "default_observed": int(np.count_nonzero(default[begin:stop] & observed_here)),
            "error_observed": int(np.count_nonzero(error[begin:stop] & observed_here)),
            "eligible": int(np.count_nonzero(eligible_here)),
            "eligible_models": int(pc.count_distinct(eligible_model).as_py()),
            "eligible_days": int(np.unique(created[begin:stop][eligible_here] // DAY_US).size),
            "observed_nonpositive_or_missing_elapsed": int(np.count_nonzero(
                observed_here & ~positive_elapsed[begin:stop]
            )),
            "observed_invalid_model": int(np.count_nonzero(
                observed_here & ~valid_model[begin:stop]
            )),
        }
        split_counts[split] = counts
        split_match &= all(counts[key] == value for key, value in EXPECTED_SPLITS[split].items())
    parameter_present = {
        name: int(pc.sum(extracted[name].is_valid().cast(pa.int64())).as_py())
        for name in PARAMETERS
    }
    return {
        "kind": "swissai_output_token_extraction_quality_v1",
        "protocol_sha256": EXPECTED_PROTOCOL_SHA256,
        "compact_rows": compact.num_rows,
        "extracted_rows": extracted.num_rows,
        "exact_source_row_order": True,
        "output_observed_all": int(np.count_nonzero(observed)),
        "output_missing_all": int(np.count_nonzero(~observed)),
        "parameter_present_all": parameter_present,
        "split_counts": split_counts,
        "frozen_split_counts_match": bool(split_match),
        "output_value_distribution_computed": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--compact", type=Path, default=DATA / "requests_sorted.parquet")
    parser.add_argument("--extracted", type=Path, default=DATA / "output_tokens_sorted.parquet")
    parser.add_argument(
        "--output", type=Path,
        default=ROOT / "artifacts/swissai_output_tokens_v1/extraction_quality.json",
    )
    args = parser.parse_args()
    result = audit(args.compact, args.extracted)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(args.output.suffix + ".tmp")
    temporary.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
    os.replace(temporary, args.output)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    if not result["frozen_split_counts_match"]:
        raise SystemExit("Observed train/validation/test counts differ from frozen protocol")


if __name__ == "__main__":
    main()
