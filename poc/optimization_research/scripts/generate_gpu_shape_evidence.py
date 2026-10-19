"""Generate GPU benchmark research evidence directly from pinned artifacts.

The saved model, the assessment, and the evidence builder run in one process.
No caller-supplied intermediate assessment is accepted. The output is an
uncalibrated same-host benchmark estimate, never a VM or deployment decision.
"""
from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path

from assess_gpu_shape import assess_gpu_shape, load_verified_bundle
from emit_gpu_shape_evidence import build_evidence
from train_performance import ROOT


DEFAULT_MODEL = ROOT / 'artifacts/performance_v1/performance_model.joblib'
DEFAULT_SOURCE = ROOT / 'data/audit_v1/measurements.parquet'


def generate_gpu_shape_evidence(request: dict, *,
                                objective_source: dict | None = None,
                                generated_at: str | None = None,
                                model_path: Path = DEFAULT_MODEL,
                                source_path: Path = DEFAULT_SOURCE) -> dict:
    """Verify the fixed artifacts, assess eight candidates, and serialize evidence.

    Hash or library-version mismatches raise before a result can be emitted.
    Unsupported but well-formed requests produce explicit ABSTAIN evidence.
    """
    bundle = load_verified_bundle(model_path, source_path)
    result = assess_gpu_shape(bundle, request)
    return build_evidence(request, result, objective_source=objective_source,
                          generated_at=generated_at)


def _write_json_atomic(output: Path, value: dict) -> None:
    """Publish a complete JSON file only if the destination is still absent."""
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
                mode='w', encoding='utf-8', dir=output.parent,
                prefix='.' + output.name + '.', suffix='.tmp',
                delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(value, stream, indent=2, ensure_ascii=False,
                      allow_nan=False)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        # A hard link creates the destination atomically and fails if another
        # file, symlink, or input artifact already owns that path.
        os.link(temporary, output)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--request', type=Path, required=True)
    parser.add_argument('--objective-source', type=Path,
                        help='optional caller-declared JSON objective source')
    parser.add_argument('--generated-at', type=str,
                        help='optional UTC RFC3339 timestamp for reproducible output')
    parser.add_argument('--model', type=Path, default=DEFAULT_MODEL,
                        help='model file, accepted only with the pinned SHA-256')
    parser.add_argument('--source', type=Path, default=DEFAULT_SOURCE,
                        help='training extract, accepted only with the pinned SHA-256')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.output.exists() or args.output.is_symlink():
            raise FileExistsError('output_path_already_exists')
        request = json.loads(args.request.read_text(encoding='utf-8'))
        objective_source = (json.loads(args.objective_source.read_text(encoding='utf-8'))
                            if args.objective_source else None)
        evidence = generate_gpu_shape_evidence(
            request, objective_source=objective_source,
            generated_at=args.generated_at, model_path=args.model,
            source_path=args.source)
        _write_json_atomic(args.output, evidence)
    except (OSError, ValueError, TypeError) as exc:
        parser.exit(2, f'gpu_shape_evidence_failed: {exc}\n')


if __name__ == '__main__':
    main()
