"""Generate versioned Philly GPU risk evidence from frozen artifacts in one call.

Only caller-declared, complete Philly trace windows are accepted for a risk
estimate. The result is research evidence and never a deployment or VM action.
"""
from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from assess_philly_gpu_risk import (
    MODEL_SPEC, NORMALIZED_SOURCE, assess_philly_gpu_risk,
)
from emit_philly_gpu_risk_evidence import build_evidence


def generate_philly_gpu_risk_evidence(
        request: Any, *, generated_at: str | None = None,
        spec_path: Path = MODEL_SPEC,
        source_path: Path = NORMALIZED_SOURCE) -> dict:
    """Verify the model/source/code, assess, then bind the exact pair."""
    result = assess_philly_gpu_risk(
        request, spec_path=spec_path, source_path=source_path)
    return build_evidence(request, result, generated_at=generated_at)


def _write_json_atomic(output: Path, value: dict) -> None:
    """Create one complete output file; never replace an existing path."""
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=output.parent,
                prefix="." + output.name + ".", suffix=".tmp",
                delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, output)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--generated-at", type=str,
                        help="UTC RFC3339 Z timestamp for reproducible output")
    parser.add_argument("--model-spec", type=Path, default=MODEL_SPEC)
    parser.add_argument("--source", type=Path, default=NORMALIZED_SOURCE)
    args = parser.parse_args()
    try:
        if args.output.exists() or args.output.is_symlink():
            raise FileExistsError("output_path_already_exists")
        request = json.loads(args.request.read_text(encoding="utf-8"))
        evidence = generate_philly_gpu_risk_evidence(
            request, generated_at=args.generated_at,
            spec_path=args.model_spec, source_path=args.source)
        _write_json_atomic(args.output, evidence)
    except (OSError, ValueError, TypeError, OverflowError) as exc:
        parser.exit(2, f"philly_gpu_risk_evidence_failed: {exc}\n")


if __name__ == "__main__":
    main()
