"""Check v0.2 GPU evidence structurally or replay the pinned model."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from generate_gpu_shape_evidence_v0_2 import (
    MODEL_PATH, SOURCE_PATH, SPLIT_PATH,
    validate_gpu_shape_evidence_v0_2,
    validate_gpu_shape_evidence_v0_2_strict,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--request', type=Path,
                        help='original request; required for --strict-replay')
    parser.add_argument('--strict-replay', action='store_true',
                        help='rerun the pinned model and compare the full evidence')
    parser.add_argument('--model', type=Path, default=MODEL_PATH,
                        help='model file, accepted only with the pinned SHA-256')
    parser.add_argument('--source', type=Path, default=SOURCE_PATH)
    parser.add_argument('--split-manifest', type=Path, default=SPLIT_PATH)
    args = parser.parse_args()
    try:
        evidence = json.loads(args.input.read_text(encoding='utf-8'))
        request = (json.loads(args.request.read_text(encoding='utf-8'))
                   if args.request else None)
        if args.strict_replay:
            validate_gpu_shape_evidence_v0_2_strict(
                evidence, request=request, model_path=args.model,
                source_path=args.source, split_path=args.split_manifest)
        else:
            validate_gpu_shape_evidence_v0_2(
                evidence, request=request, source_path=args.source,
                split_path=args.split_manifest)
    except (OSError, ValueError, TypeError) as exc:
        parser.exit(2, f'gpu_shape_evidence_v0_2_invalid: {exc}\n')
    print('gpu_shape_evidence_v0_2_valid')


if __name__ == '__main__':
    main()
