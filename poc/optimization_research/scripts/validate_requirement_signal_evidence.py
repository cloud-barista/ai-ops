"""Validate and replay offline requirement signal evidence using fixed models.

Replay proves consistency with the local pinned research artifacts, not the
truth of caller-declared registration metadata or deployment suitability.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

import assess_requirement_signals as assessor
from generate_requirement_signal_evidence import (
    EVIDENCE_TYPE, SCHEMA_VERSION, SCOPE, canonical_bytes,
    checked_generated_at, evidence_id, read_json, validate_schema,
)


def validate_requirement_signal_evidence(
        evidence: Any, *, request: Any | None = None,
        source_path: Path = assessor.SOURCE,
        vram_path: Path = assessor.VRAM_MODEL,
        load_path: Path = assessor.LOAD_MODEL) -> None:
    """Check the complete record, then independently rerun the fixed models."""
    canonical_bytes(evidence)
    validate_schema(evidence)
    if (evidence['evidence_type'] != EVIDENCE_TYPE
            or evidence['schema_version'] != SCHEMA_VERSION
            or evidence['scope'] != SCOPE):
        raise ValueError('unexpected_evidence_contract')
    saved_request = evidence['request']
    if assessor._request_errors(saved_request):
        raise ValueError('invalid_saved_requirement_request')
    request_digest = hashlib.sha256(canonical_bytes(saved_request)).hexdigest()
    if evidence['request_sha256'] != request_digest:
        raise ValueError('request_sha256_mismatch')
    if request is not None and canonical_bytes(request) != canonical_bytes(saved_request):
        raise ValueError('original_request_mismatch')
    checked_generated_at(evidence['generated_at'], saved_request['decision_at'])
    if evidence['evidence_id'] != evidence_id(evidence):
        raise ValueError('evidence_id_mismatch')

    result = evidence['result']
    if (result['scope'] != assessor.SCOPE
            or result['source_sha256'] != assessor.SOURCE_SHA256
            or result['training_sklearn_version'] != assessor.SKLEARN_VERSION
            or result['metadata_provenance'] != 'caller_declared'
            or not assessor._same_json_value(result['hardware_profile'],
                                             assessor.EXPECTED_PROFILE)):
        raise ValueError('unexpected_assessor_provenance')
    for name, descriptor in assessor.SIGNALS.items():
        signal = result['signals'][name]
        if (signal['unit'] != descriptor['unit']
                or signal['target_definition'] != descriptor['target_definition']
                or signal['artifact_sha256'] != descriptor['artifact_sha256']):
            raise ValueError('unexpected_signal_descriptor:' + name)

    bundles = assessor.load_verified_bundles(
        vram_path=vram_path, load_path=load_path, source_path=source_path)
    replay = assessor.assess_requirement_signals(bundles, saved_request)
    if canonical_bytes(replay) != canonical_bytes(result):
        raise ValueError('assessor_replay_mismatch')


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', required=True, type=Path)
    parser.add_argument('--request', type=Path,
                        help='Optional original request JSON for independent matching')
    parser.add_argument('--source', type=Path, default=assessor.SOURCE)
    parser.add_argument('--vram-model', type=Path, default=assessor.VRAM_MODEL)
    parser.add_argument('--load-model', type=Path, default=assessor.LOAD_MODEL)
    args = parser.parse_args()
    try:
        evidence = read_json(args.input)
        request = read_json(args.request) if args.request else None
        validate_requirement_signal_evidence(
            evidence, request=request, source_path=args.source,
            vram_path=args.vram_model, load_path=args.load_model)
    except (OSError, ValueError, TypeError, OverflowError) as exc:
        parser.exit(2, f'requirement_signal_evidence_invalid: {exc}\n')
    print('requirement_signal_evidence_valid')


if __name__ == '__main__':
    main()
