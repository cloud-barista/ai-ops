"""Posthoc uncalibrated-score diagnostic; never changes the frozen selection gate."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import joblib
import numpy as np
import pandas as pd


SCRIPT = Path(__file__).resolve().parent / "evaluate_google_2011_early_feedback.py"
SPEC = importlib.util.spec_from_file_location("frozen_early_feedback", SCRIPT)
study = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = study
SPEC.loader.exec_module(study)


def main() -> None:
    study.check_freeze()
    validation_path = study.OUT / "validation.json"
    frozen_result = json.loads(validation_path.read_text())
    model_path = study.OUT / "model.joblib"
    if study.sha256(model_path) != frozen_result["model_sha256"]:
        raise ValueError("Frozen model hash mismatch")
    frame, info = study.extract_shard(study.VALIDATION)
    if (info["eligible_tasks"] != frozen_result["validation"]["eligible_tasks"]
            or info["eligible_jobs"] != frozen_result["validation"]["eligible_jobs"]):
        raise ValueError("Validation sample changed")
    bundle = joblib.load(model_path)
    raw = study.predict_raw(frame, bundle)
    metrics = {}
    for method in study.METHODS:
        rows = pd.DataFrame({"job": frame.job.to_numpy()})
        joint = np.ones(len(frame), dtype=bool)
        for axis in study.AXES:
            y, prediction = study.target(frame, axis), raw[method][axis]
            rows[axis] = study.pinball(y, prediction)
            joint &= y <= prediction
        rows["joint"] = joint.astype(float)
        rows["composite"] = (
            rows["cpu"] / bundle["scales"]["cpu"]
            + rows["memory"] / bundle["scales"]["memory"]) / 2
        job = rows.groupby("job", sort=True).mean(numeric_only=True)
        metrics[method] = {"job_equal_composite": float(job.composite.mean()),
                           "job_equal_cpu_pinball": float(job.cpu.mean()),
                           "job_equal_memory_pinball": float(job.memory.mean()),
                           "job_equal_joint_coverage": float(job.joint.mean())}
    result = dict(label="posthoc_uncalibrated_diagnostic_not_gate",
                  validation_sha256=study.sha256(validation_path),
                  model_sha256=study.sha256(model_path),
                  script_sha256=study.sha256(Path(__file__)),
                  validation_tasks=len(frame), validation_jobs=int(frame.job.nunique()),
                  metrics=metrics)
    study.write_json(study.OUT / "raw_posthoc.json", result)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
