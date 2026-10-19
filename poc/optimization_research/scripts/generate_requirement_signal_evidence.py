"""Create request-bound offline evidence from the fixed requirement models.

The two signals remain independent research estimates. This module does not
infer minimum VM resources, authorize deployment, or decide scale actions.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from jsonschema import Draft7Validator, FormatChecker

import assess_requirement_signals as assessor


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / 'contracts/requirement_signal_evidence_v0.1.0.schema.json'
EVIDENCE_TYPE = 'khu.research.requirement_signal_evidence'
SCHEMA_VERSION = '0.1.0'
SCOPE = {
    'kind': 'offline_application_requirement_signals',
    'deployment_authority': False,
    'input_metadata_verified': False,
}
UTC_RE = re.compile(r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$')


def canonical_bytes(value: Any) -> bytes:
    try:
        return json.dumps(value, sort_keys=True, separators=(',', ':'),
                          ensure_ascii=False, allow_nan=False).encode('utf-8')
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError('non_json_or_nonfinite_input') from exc


def read_json(path: Path) -> Any:
    def unique_pairs(pairs: list[tuple[str, Any]]) -> dict:
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('duplicate_json_key:' + key)
            result[key] = value
        return result
    return json.loads(path.read_text(encoding='utf-8'),
                      object_pairs_hook=unique_pairs)


def checked_generated_at(value: str | None, decision_at: str) -> str:
    if value is None:
        value = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
    if not isinstance(value, str) or not UTC_RE.fullmatch(value):
        raise ValueError('generated_at_must_be_rfc3339_utc_z')
    try:
        generated = datetime.fromisoformat(value.replace('Z', '+00:00'))
        decision = datetime.fromisoformat(decision_at.replace('Z', '+00:00'))
    except ValueError as exc:
        raise ValueError('invalid_evidence_time') from exc
    if generated < decision:
        raise ValueError('evidence_generated_before_decision')
    return value


def evidence_id(value: dict) -> str:
    payload = {key: item for key, item in value.items()
               if key not in {'evidence_id', 'generated_at'}}
    return 'sha256:' + hashlib.sha256(canonical_bytes(payload)).hexdigest()


def validate_schema(value: Any) -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding='utf-8'))
    Draft7Validator.check_schema(schema)
    error = next(Draft7Validator(schema, format_checker=FormatChecker()).iter_errors(value), None)
    if error is not None:
        raise ValueError('evidence_schema_validation_failed:' + error.message)


def generate_requirement_signal_evidence(
        request: Any, *, generated_at: str | None = None,
        source_path: Path = assessor.SOURCE,
        vram_path: Path = assessor.VRAM_MODEL,
        load_path: Path = assessor.LOAD_MODEL) -> dict:
    """Assess one valid declared request and bind the exact request/result.

    The artifact loader and assessor run in one call. The saved evidence is
    self-contained, but its hash is a consistency check rather than a signature.
    """
    canonical_bytes(request)
    frozen_request = copy.deepcopy(request)
    reasons = assessor._request_errors(frozen_request)
    if reasons:
        raise ValueError('invalid_requirement_request:' + ','.join(reasons))
    bundles = assessor.load_verified_bundles(
        vram_path=vram_path, load_path=load_path, source_path=source_path)
    result = assessor.assess_requirement_signals(bundles, frozen_request)
    value = {
        'evidence_type': EVIDENCE_TYPE,
        'schema_version': SCHEMA_VERSION,
        'request_sha256': hashlib.sha256(canonical_bytes(frozen_request)).hexdigest(),
        'generated_at': checked_generated_at(generated_at, frozen_request['decision_at']),
        'scope': dict(SCOPE),
        'request': frozen_request,
        'result': result,
    }
    value['evidence_id'] = evidence_id(value)
    validate_schema(value)
    return value


def write_json_atomic(output: Path, value: dict) -> None:
    """Write a complete new evidence file without replacing an existing one."""
    if output.exists() or output.is_symlink():
        raise FileExistsError('output_path_already_exists')
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
                mode='w', encoding='utf-8', dir=output.parent,
                prefix='.' + output.name + '.', suffix='.tmp',
                delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, output)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--request', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--generated-at', type=str)
    parser.add_argument('--source', type=Path, default=assessor.SOURCE)
    parser.add_argument('--vram-model', type=Path, default=assessor.VRAM_MODEL)
    parser.add_argument('--load-model', type=Path, default=assessor.LOAD_MODEL)
    args = parser.parse_args()
    try:
        if args.output.exists() or args.output.is_symlink():
            raise FileExistsError('output_path_already_exists')
        request = read_json(args.request)
        result = generate_requirement_signal_evidence(
            request, generated_at=args.generated_at, source_path=args.source,
            vram_path=args.vram_model, load_path=args.load_model)
        write_json_atomic(args.output, result)
    except (OSError, ValueError, TypeError, OverflowError) as exc:
        parser.exit(2, f'requirement_signal_evidence_failed: {exc}\n')


if __name__ == '__main__':
    main()
