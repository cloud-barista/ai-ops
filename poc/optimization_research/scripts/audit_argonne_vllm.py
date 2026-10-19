"""Pin and audit Argonne LLM-Inference-Bench for cross-hardware research."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "data/external/argonne_llm_inference_bench/All_results.csv"
LICENSE = ROOT / "data/external/argonne_llm_inference_bench/LICENSE"
OUT = ROOT / "artifacts/argonne_vllm_audit_v1"
SOURCE_SHA256 = "631a12e08c786ce2d4bdfdc0f67b93234360682cb4b36c93806270ddcfd58412"
LICENSE_SHA256 = "885d0459f105e9e5362ebf6da918eb8d0866948c559e03e6bfcebcffe4cc5c96"
COLUMNS = ["Hardware", "Num of Hardware", "Framework", "Model",
           "Input Output Length", "Batch Size", "Latency", "Throughput"]
WORKLOAD = ["Model", "Input Output Length", "Batch Size"]
SHAPE = ["Hardware", "Num of Hardware"]


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def audit() -> tuple[dict, pd.DataFrame]:
    for path, expected in ((SOURCE, SOURCE_SHA256), (LICENSE, LICENSE_SHA256)):
        if sha256(path) != expected:
            raise ValueError(f"pinned file changed: {path}")
    frame = pd.read_csv(SOURCE)
    if list(frame.columns) != COLUMNS or frame.isna().any().any():
        raise ValueError("unexpected columns or missing source values")
    if frame.duplicated().any() or len(frame) != 4772:
        raise ValueError("source exact duplicates or row count changed")
    for name in ("Num of Hardware", "Input Output Length", "Batch Size", "Latency", "Throughput"):
        values = frame[name].to_numpy(dtype=float)
        if not np.isfinite(values).all() or np.any(values <= 0):
            raise ValueError(f"invalid positive source metric: {name}")
    frame.insert(0, "source_row_index", np.arange(len(frame), dtype=int))
    expected_throughput = (frame["Batch Size"] * 2 * frame["Input Output Length"]
                           / frame["Latency"])
    relative_error = np.abs(frame["Throughput"] / expected_throughput - 1.0)
    formula_by_framework = {}
    for name, part in frame.groupby("Framework"):
        residual = relative_error.loc[part.index]
        formula_by_framework[str(name)] = {
            "rows": len(part), "matches_paper_formula_rtol_1e_4": int((residual <= 1e-4).sum()),
            "max_relative_deviation": float(residual.max()),
        }
    vllm = frame[(frame.Framework == "vLLM") &
                 (frame["Batch Size"].isin((1, 16, 32, 64)))].copy()
    if len(vllm) != 1818 or not (relative_error.loc[vllm.index] <= 1e-4).all():
        raise ValueError("vLLM paper-grid or throughput identity changed")
    counts = vllm.groupby(WORKLOAD + SHAPE, dropna=False).size().rename("repeats")
    ambiguous_keys = counts[counts > 1].reset_index()[WORKLOAD + SHAPE]
    ambiguous = vllm.merge(ambiguous_keys, on=WORKLOAD + SHAPE, how="inner")
    clean = vllm.merge(ambiguous_keys.assign(ambiguous=True),
                       on=WORKLOAD + SHAPE, how="left")
    clean = clean[clean.ambiguous.isna()].drop(columns="ambiguous")
    if len(ambiguous_keys) != 57 or len(ambiguous) != 114 or len(clean) != 1704:
        raise ValueError("ambiguous duplicate coverage changed")
    hardware_counts = clean.groupby(WORKLOAD).Hardware.nunique().rename("n_hardware")
    paired_keys = hardware_counts[hardware_counts >= 2].reset_index()[WORKLOAD]
    paired = clean.merge(paired_keys, on=WORKLOAD, how="inner")
    group_count = paired[WORKLOAD].drop_duplicates().groupby("Model").size()
    primary_models = sorted(group_count[group_count >= 9].index.tolist())
    primary = paired[paired.Model.isin(primary_models)].sort_values("source_row_index")
    if (len(paired) != 1579 or len(paired_keys) != 175 or
            len(primary_models) != 9 or len(primary) != 1567 or
            len(primary[WORKLOAD].drop_duplicates()) != 169):
        raise ValueError("cross-hardware research support changed")
    if primary.duplicated(WORKLOAD + SHAPE).any():
        raise ValueError("duplicate hardware shape remains")

    result = {
        "source_sha256": SOURCE_SHA256, "license_sha256": LICENSE_SHA256,
        "source_rows": len(frame), "frameworks": frame.Framework.nunique(),
        "models": frame.Model.nunique(), "physical_shapes": len(frame[SHAPE].drop_duplicates()),
        "throughput_formula_by_framework": formula_by_framework,
        "vllm_paper_grid_rows": len(vllm),
        "ambiguous_duplicate_keys": len(ambiguous_keys),
        "ambiguous_rows_excluded": len(ambiguous),
        "vllm_clean_rows": len(clean),
        "cross_hardware_groups": len(paired_keys),
        "cross_hardware_rows": len(paired),
        "primary_models": primary_models,
        "primary_groups_per_model": {str(k): int(v) for k, v in group_count.loc[primary_models].items()},
        "primary_groups": len(primary[WORKLOAD].drop_duplicates()),
        "primary_rows": len(primary),
        "no_failure_or_oom_label": True,
        "no_publication_timestamp_per_row": True,
    }
    return result, primary


def main() -> None:
    result, primary = audit()
    OUT.mkdir(parents=True, exist_ok=True)
    rows = OUT / "eligible_vllm.csv"
    primary.to_csv(rows, index=False)
    result["eligible_csv_sha256"] = sha256(rows)
    result["audit_script_sha256"] = sha256(Path(__file__))
    (OUT / "audit.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"source_rows": result["source_rows"],
                      "primary_rows": result["primary_rows"],
                      "primary_groups": result["primary_groups"],
                      "primary_models": len(result["primary_models"])}, sort_keys=True))


if __name__ == "__main__":
    main()
